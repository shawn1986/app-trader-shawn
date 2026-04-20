from __future__ import annotations

from pathlib import Path
from typing import Any

from trader_shawn.monitoring.state_store import StateStore


def read_dashboard_snapshot(path: str | Path) -> dict[str, Any]:
    return StateStore(Path(path)).load()


def build_dashboard_snapshot(*, last_cycle: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "idle" if last_cycle is None else "updated",
        "last_cycle": last_cycle or {},
    }


def update_dashboard_state(path: str | Path, *, last_cycle: dict[str, Any]) -> dict[str, Any]:
    snapshot = build_dashboard_snapshot(last_cycle=last_cycle)
    StateStore(Path(path)).save(snapshot)
    return snapshot
