from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse


SnapshotProvider = Callable[[], dict[str, Any]]


def create_war_room_app(snapshot_provider: SnapshotProvider | None = None) -> FastAPI:
    app = FastAPI()
    provider = snapshot_provider or _default_snapshot

    @app.get("/api/war-room/snapshot")
    def get_war_room_snapshot() -> JSONResponse:
        return JSONResponse(provider())

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
