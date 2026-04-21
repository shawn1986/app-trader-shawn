from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
import time
from typing import Any, Callable

from fastapi import Body, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trader_shawn.app import build_cli_runtime
from trader_shawn.monitoring.audit_logger import AuditLogger
from trader_shawn.monitoring.dashboard_api import read_dashboard_snapshot
from trader_shawn.war_room.commands import ArmedSessionStore, run_runtime_command
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
    provider = snapshot_provider or create_default_snapshot_provider()
    runner = command_runner or run_runtime_command
    armed_sessions = ArmedSessionStore()

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
        except ValueError:
            return JSONResponse({"reason": "unsupported_command"}, status_code=404)

        return JSONResponse(result)

    return app


def create_default_snapshot_provider(
    dashboard_state_path: Path | None = None,
    audit_db_path: Path | None = None,
) -> SnapshotProvider:
    runtime = build_cli_runtime()
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
        position_events = audit_logger.fetch_recent_position_events()
        account_snapshot, broker_health = _probe_broker_health(runtime)
        return build_war_room_snapshot(
            dashboard_state=dashboard_state,
            account_snapshot=account_snapshot,
            managed_positions=managed_positions,
            position_events=position_events,
            broker_health=broker_health,
            now=datetime.now(UTC),
        )

    return provider


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
