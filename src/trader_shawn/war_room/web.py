from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
import inspect
from pathlib import Path
import threading
import time
from typing import Any, Callable

from fastapi import Body, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trader_shawn.app import build_cli_runtime
from trader_shawn.monitoring.audit_logger import AuditLogger
from trader_shawn.monitoring.dashboard_api import read_dashboard_snapshot
from trader_shawn.war_room.commands import (
    ArmedSessionStore,
    UnsupportedWarRoomCommand,
    run_runtime_command,
)
from trader_shawn.war_room.service import build_war_room_snapshot


SnapshotProvider = Callable[[], dict[str, Any]]
CommandRunner = Callable[[str, dict[str, Any] | None], dict[str, Any]]


def create_war_room_app(
    snapshot_provider: SnapshotProvider | None = None,
    command_runner: CommandRunner | None = None,
) -> FastAPI:
    app = FastAPI()
    war_room_dir = Path(__file__).resolve().parent
    app.mount(
        "/static",
        StaticFiles(directory=war_room_dir / "static"),
        name="static",
    )
    templates = Jinja2Templates(directory=str(war_room_dir / "templates"))
    if snapshot_provider is None and command_runner is None:
        provider, runner = _lazy_shared_default_provider_and_runner()
    else:
        provider = snapshot_provider or _lazy_default_snapshot_provider()
        runner = command_runner or run_runtime_command
    armed_sessions = ArmedSessionStore()

    @app.get("/")
    def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/war-room")

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/war-room")
    def war_room_shell(request: Request):
        return templates.TemplateResponse(request, "war_room.html")

    @app.get("/api/war-room/snapshot")
    def get_war_room_snapshot() -> JSONResponse:
        return JSONResponse(provider())

    @app.post("/api/war-room/arm")
    def arm_war_room(payload: dict[str, Any] = Body(default_factory=dict)) -> Response:
        if payload.get("phrase") != "ARM":
            return JSONResponse({"reason": "invalid_arm_phrase"}, status_code=403)

        token = armed_sessions.arm()
        response = Response(status_code=204)
        response.set_cookie("war_room_armed", token, httponly=True, samesite="strict")
        return response

    @app.post("/api/war-room/commands/{command_name}")
    def run_command(
        command_name: str,
        request: Request,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> JSONResponse:
        if not armed_sessions.is_armed(request.cookies.get("war_room_armed")):
            return JSONResponse({"reason": "armed_mode_required"}, status_code=403)

        if command_name == "trade" and payload.get("confirmed") is not True:
            return JSONResponse({"reason": "trade_confirmation_required"}, status_code=409)

        try:
            result = runner(command_name, payload)
        except UnsupportedWarRoomCommand as exc:
            return JSONResponse(
                {"reason": "unsupported_command", "command": exc.command},
                status_code=404,
            )

        return JSONResponse(result)

    return app


def create_default_snapshot_provider(
    dashboard_state_path: Path | None = None,
    audit_db_path: Path | None = None,
    runtime: Any | None = None,
) -> SnapshotProvider:
    runtime = runtime or build_cli_runtime()
    resolved_dashboard_state_path = dashboard_state_path or runtime.dashboard_state_path
    resolved_audit_db_path = audit_db_path or runtime.settings.audit_db_path
    mode = str(getattr(runtime.settings, "mode", "paper"))
    audit_logger = AuditLogger(resolved_audit_db_path)

    def provider() -> dict[str, Any]:
        dashboard_state = (
            read_dashboard_snapshot(resolved_dashboard_state_path)
            if resolved_dashboard_state_path is not None
            else {}
        )
        managed_positions = audit_logger.fetch_active_managed_positions(mode=mode)
        position_events = _fetch_latest_events_for_active_positions(
            audit_logger,
            managed_positions=managed_positions,
        )
        mission_log_events = audit_logger.fetch_recent_position_events()
        account_snapshot, broker_health = _probe_broker_health(runtime)
        return build_war_room_snapshot(
            dashboard_state=dashboard_state,
            account_snapshot=account_snapshot,
            managed_positions=managed_positions,
            position_events=position_events,
            mission_log_events=mission_log_events,
            broker_health=broker_health,
            now=datetime.now(UTC),
        )

    return provider


def _lazy_shared_default_provider_and_runner() -> tuple[SnapshotProvider, CommandRunner]:
    runtime: Any | None = None
    provider: SnapshotProvider | None = None
    init_lock = threading.RLock()
    runtime_use_lock = threading.Lock()
    provider_factory = create_default_snapshot_provider
    provider_accepts_runtime = "runtime" in inspect.signature(provider_factory).parameters

    def _ensure_runtime() -> Any:
        nonlocal runtime
        if runtime is not None:
            return runtime
        with init_lock:
            if runtime is None:
                runtime = build_cli_runtime()
        return runtime

    def _ensure_provider() -> SnapshotProvider:
        nonlocal provider
        if provider is not None:
            return provider
        with init_lock:
            if provider is None:
                if provider_accepts_runtime:
                    provider = provider_factory(runtime=_ensure_runtime())
                else:
                    provider = provider_factory()
        return provider

    def lazy_provider() -> dict[str, Any]:
        try:
            resolved_provider = _ensure_provider()
        except Exception as exc:
            return _degraded_snapshot(
                reason="snapshot_provider_unavailable",
                exc=exc,
            )

        try:
            with runtime_use_lock:
                return resolved_provider()
        except Exception as exc:
            return _degraded_snapshot(reason="snapshot_provider_error", exc=exc)

    def lazy_runner(command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resolved_runtime = _ensure_runtime()
        except Exception:
            return run_runtime_command(command, payload)
        with runtime_use_lock:
            return run_runtime_command(command, payload, runtime=resolved_runtime)

    return lazy_provider, lazy_runner


def _lazy_default_snapshot_provider() -> SnapshotProvider:
    provider: SnapshotProvider | None = None

    def lazy_provider() -> dict[str, Any]:
        nonlocal provider
        if provider is None:
            try:
                provider = create_default_snapshot_provider()
            except Exception as exc:
                return _degraded_snapshot(
                    reason="snapshot_provider_unavailable",
                    exc=exc,
                )

        try:
            return provider()
        except Exception as exc:
            return _degraded_snapshot(reason="snapshot_provider_error", exc=exc)

    return lazy_provider


def _degraded_snapshot(*, reason: str, exc: Exception | None) -> dict[str, Any]:
    detail = (
        {"type": type(exc).__name__, "message": str(exc)}
        if exc is not None
        else None
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "threat_level": "warning",
        "command_status": {
            "broker": {
                "state": "degraded",
                "freshness": "unknown",
                "checked_at": None,
                "latency_ms": None,
                "message": reason,
            },
            "dashboard_status": "error",
            "last_cycle": {},
            "provider_error": detail,
        },
        "risk_deck": {
            "net_liquidation": 0.0,
            "unrealized_pnl": 0.0,
            "open_risk": 0.0,
            "new_positions_today": 0,
            "active_managed_positions": 0,
        },
        "hot_positions": [],
        "mission_log": [],
        "threat_rail": {
            "cycle_status": "error",
            "reason": reason,
            "manual_intervention_required": False,
            "fingerprints": [],
        },
        "account_rail": {
            "net_liquidation": 0.0,
            "unrealized_pnl": 0.0,
            "open_risk": 0.0,
            "new_positions_today": 0,
        },
    }


def _fetch_latest_events_for_active_positions(
    audit_logger: AuditLogger,
    *,
    managed_positions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest_events: list[dict[str, Any]] = []
    for position in managed_positions:
        position_id = str(position.get("position_id", "")).strip()
        if not position_id:
            continue
        position_events = audit_logger.fetch_position_events(position_id)
        if not position_events:
            continue
        latest = position_events[-1]
        latest_events.append(
            {
                "position_id": str(latest.get("position_id", position_id)),
                "event_type": str(latest.get("event_type", "")),
                "payload_json": latest.get("payload", {}),
                "created_at": latest.get("created_at"),
            }
        )
    return latest_events


def _probe_broker_health(runtime: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    checked_at = datetime.now(UTC).isoformat()
    account_service = getattr(runtime, "account_service", None)
    fetch_account_snapshot = getattr(account_service, "fetch_account_snapshot", None)
    if not callable(fetch_account_snapshot):
        return (
            {},
            {
                "connected": False,
                "latency_ms": None,
                "checked_at": checked_at,
                "message": "account_service_unavailable",
            },
        )

    started = time.perf_counter()
    try:
        raw_account_snapshot = fetch_account_snapshot()
    except Exception as exc:
        return (
            {},
            {
                "connected": False,
                "latency_ms": None,
                "checked_at": checked_at,
                "message": str(exc),
            },
        )
    latency_ms = max(0, int((time.perf_counter() - started) * 1000))
    return (
        _coerce_account_snapshot(raw_account_snapshot),
        {
            "connected": True,
            "latency_ms": latency_ms,
            "checked_at": checked_at,
            "message": "",
        },
    )


def _coerce_account_snapshot(snapshot: Any) -> dict[str, Any]:
    if isinstance(snapshot, dict):
        source = snapshot
    elif is_dataclass(snapshot):
        source = asdict(snapshot)
    else:
        source = {
            "net_liquidation": getattr(snapshot, "net_liquidation", None),
            "unrealized_pnl": getattr(snapshot, "unrealized_pnl", None),
            "open_risk": getattr(snapshot, "open_risk", None),
            "new_positions_today": getattr(snapshot, "new_positions_today", None),
        }
    return {
        "net_liquidation": _as_number(source.get("net_liquidation")),
        "unrealized_pnl": _as_number(source.get("unrealized_pnl")),
        "open_risk": _as_number(source.get("open_risk")),
        "new_positions_today": _as_int(source.get("new_positions_today")),
    }


def _as_number(value: Any) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _as_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0
