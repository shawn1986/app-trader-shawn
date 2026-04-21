from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trader_shawn.war_room.commands import ArmedSessionStore, run_runtime_command


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
    provider = snapshot_provider or _default_snapshot
    runner = command_runner or run_runtime_command
    armed_sessions = ArmedSessionStore()

    @app.get("/war-room")
    def war_room_shell(request: Request):
        return templates.TemplateResponse(
            "war_room.html",
            {"request": request},
        )

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


def _default_snapshot() -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "threat_level": "nominal",
        "command_status": {},
        "risk_deck": {},
        "hot_positions": [],
        "mission_log": [],
        "threat_rail": {},
    }
