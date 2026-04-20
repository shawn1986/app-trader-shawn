from __future__ import annotations

from pathlib import Path
from typing import Any

from trader_shawn.monitoring.state_store import StateStore, StateStoreError


def _snapshot_shape(
    *,
    status: str = "idle",
    last_cycle: dict[str, Any] | None = None,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "last_cycle": dict(last_cycle or {}),
        "error": error,
    }


def read_dashboard_snapshot(path: str | Path) -> dict[str, Any]:
    try:
        loaded = StateStore(Path(path)).load()
    except StateStoreError as exc:
        return _snapshot_shape(
            status="error",
            error={
                "type": type(exc).__name__,
                "message": str(exc),
            },
        )

    if not loaded:
        return _snapshot_shape()

    return _snapshot_shape(
        status=str(loaded.get("status", "idle")),
        last_cycle=loaded.get("last_cycle"),
        error=loaded.get("error"),
    )


def build_dashboard_snapshot(*, last_cycle: dict[str, Any] | None = None) -> dict[str, Any]:
    return _snapshot_shape(
        status="idle" if last_cycle is None else "updated",
        last_cycle=last_cycle,
    )


def update_dashboard_state(path: str | Path, *, last_cycle: dict[str, Any]) -> dict[str, Any]:
    snapshot = build_dashboard_snapshot(last_cycle=last_cycle)
    StateStore(Path(path)).save(snapshot)
    return snapshot
