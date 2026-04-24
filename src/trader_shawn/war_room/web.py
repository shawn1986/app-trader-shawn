from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
import asyncio
import inspect
from pathlib import Path
import threading
import time
from typing import Any, Callable
from uuid import uuid4

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
CommandRunner = Callable[..., dict[str, Any]]
SUPPORTED_COMMANDS = frozenset({"scan", "decide", "manage", "trade"})


class CommandExecutionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = self._idle_state()

    def _idle_state(self) -> dict[str, Any]:
        return {
            "status": "idle",
            "job_id": None,
            "command": None,
            "started_at": None,
            "updated_at": None,
            "completed_at": None,
            "current_message": None,
            "progress": {"current": 0, "total": None, "unit": "steps"},
            "events": [],
            "result": None,
            "error": None,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def start(self, command: str) -> dict[str, Any] | None:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            if self._state.get("status") == "running":
                return None
            self._state = {
                "status": "running",
                "job_id": uuid4().hex,
                "command": command,
                "started_at": now,
                "updated_at": now,
                "completed_at": None,
                "current_message": f"{command.upper()} command accepted.",
                "progress": {"current": 0, "total": None, "unit": "steps"},
                "events": [
                    {
                        "timestamp": now,
                        "stage": "command_started",
                        "message": f"{command.upper()} command accepted.",
                    }
                ],
                "result": None,
                "error": None,
            }
            return deepcopy(self._state)

    def record(self, job_id: str, payload: dict[str, Any]) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with self._lock:
            if self._state.get("job_id") != job_id:
                return
            self._state["updated_at"] = timestamp
            message = payload.get("message")
            if isinstance(message, str) and message:
                self._state["current_message"] = message

            current = payload.get("current")
            total = payload.get("total")
            unit = payload.get("unit")
            progress = self._state.setdefault(
                "progress",
                {"current": 0, "total": None, "unit": "steps"},
            )
            if isinstance(current, int) and not isinstance(current, bool):
                progress["current"] = current
            if isinstance(total, int) and not isinstance(total, bool):
                progress["total"] = total
            if isinstance(unit, str) and unit:
                progress["unit"] = unit

            event = {
                "timestamp": timestamp,
                **{
                    key: value
                    for key, value in payload.items()
                    if key not in {"result", "error"}
                },
            }
            events = self._state.setdefault("events", [])
            events.append(event)
            if len(events) > 16:
                del events[:-16]

    def finish(self, job_id: str, result: dict[str, Any]) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with self._lock:
            if self._state.get("job_id") != job_id:
                return
            self._state["status"] = "completed"
            self._state["updated_at"] = timestamp
            self._state["completed_at"] = timestamp
            self._state["result"] = result
            self._state["error"] = None
            self._state["events"].append(
                {
                    "timestamp": timestamp,
                    "stage": "command_completed",
                    "message": (
                        f"{str(self._state.get('command') or '').upper()} command "
                        f"completed with status {result.get('status', 'unknown')}."
                    ),
                    "result_status": result.get("status"),
                }
            )
            if len(self._state["events"]) > 16:
                del self._state["events"][:-16]

    def fail(self, job_id: str, exc: Exception) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with self._lock:
            if self._state.get("job_id") != job_id:
                return
            self._state["status"] = "failed"
            self._state["updated_at"] = timestamp
            self._state["completed_at"] = timestamp
            self._state["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            self._state["events"].append(
                {
                    "timestamp": timestamp,
                    "stage": "command_failed",
                    "message": (
                        f"{str(self._state.get('command') or '').upper()} command "
                        f"failed: {exc}"
                    ),
                    "error_type": type(exc).__name__,
                }
            )
            if len(self._state["events"]) > 16:
                del self._state["events"][:-16]


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
    command_store = CommandExecutionStore()
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
        _ensure_thread_event_loop()
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

        if command_name not in SUPPORTED_COMMANDS:
            return JSONResponse(
                {"reason": "unsupported_command", "command": command_name},
                status_code=404,
            )

        if command_name == "trade" and payload.get("confirmed") is not True:
            return JSONResponse({"reason": "trade_confirmation_required"}, status_code=409)

        started = command_store.start(command_name)
        if started is None:
            return JSONResponse(
                {
                    "reason": "command_in_progress",
                    **command_store.snapshot(),
                },
                status_code=409,
            )

        if payload.get("async") is True:
            async_payload = dict(payload)
            async_payload.pop("async", None)
            worker = threading.Thread(
                target=_run_async_command,
                args=(command_store, started["job_id"], runner, command_name, async_payload),
                daemon=True,
            )
            worker.start()
            return JSONResponse(
                {
                    "status": "accepted",
                    "command": command_name,
                    "job_id": started["job_id"],
                },
                status_code=202,
            )

        _ensure_thread_event_loop()
        try:
            result = _run_command_runner(
                runner,
                command_name,
                payload,
                progress_callback=lambda event: command_store.record(started["job_id"], event),
            )
        except UnsupportedWarRoomCommand as exc:
            command_store.fail(started["job_id"], exc)
            return JSONResponse(
                {"reason": "unsupported_command", "command": exc.command},
                status_code=404,
            )
        except Exception as exc:
            command_store.fail(started["job_id"], exc)
            raise

        command_store.finish(started["job_id"], result)

        return JSONResponse(result)

    @app.get("/api/war-room/commands/status")
    def get_command_status() -> JSONResponse:
        return JSONResponse(command_store.snapshot())

    return app


def _ensure_thread_event_loop() -> asyncio.AbstractEventLoop:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop

    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


def _run_command_runner(
    runner: CommandRunner,
    command_name: str,
    payload: dict[str, Any] | None,
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if _callable_accepts_keyword(runner, "progress_callback"):
        return runner(command_name, payload, progress_callback=progress_callback)
    return runner(command_name, payload)


def _callable_accepts_keyword(func: Callable[..., Any], name: str) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == name:
            return True
    return False


def _run_async_command(
    command_store: CommandExecutionStore,
    job_id: str,
    runner: CommandRunner,
    command_name: str,
    payload: dict[str, Any],
) -> None:
    _ensure_thread_event_loop()
    try:
        result = _run_command_runner(
            runner,
            command_name,
            payload,
            progress_callback=lambda event: command_store.record(job_id, event),
        )
    except Exception as exc:
        command_store.fail(job_id, exc)
        return
    command_store.finish(job_id, result)


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

    def lazy_runner(
        command: str,
        payload: dict[str, Any] | None = None,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        runtime_command_accepts_progress = _callable_accepts_keyword(
            run_runtime_command,
            "progress_callback",
        )
        try:
            resolved_runtime = _ensure_runtime()
        except Exception:
            if runtime_command_accepts_progress:
                return run_runtime_command(
                    command,
                    payload,
                    progress_callback=progress_callback,
                )
            return run_runtime_command(command, payload)
        with runtime_use_lock:
            if runtime_command_accepts_progress:
                return run_runtime_command(
                    command,
                    payload,
                    runtime=resolved_runtime,
                    progress_callback=progress_callback,
                )
            return run_runtime_command(
                command,
                payload,
                runtime=resolved_runtime,
            )

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
