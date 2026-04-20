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
        "status": _normalize_snapshot_status(status),
        "last_cycle": _normalize_last_cycle(last_cycle),
        "error": _normalize_error(error),
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
        status=loaded.get("status", "idle"),
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


def _normalize_snapshot_status(status: Any) -> str:
    return status if isinstance(status, str) and status else "idle"


def _normalize_last_cycle(last_cycle: Any) -> dict[str, Any]:
    if not isinstance(last_cycle, dict):
        return {}

    normalized: dict[str, Any] = {}
    for key in ("status", "reason", "action", "error_type", "message"):
        value = last_cycle.get(key)
        if isinstance(value, str) and value:
            normalized[key] = value

    if "payload" in last_cycle:
        payload = last_cycle.get("payload")
        normalized["payload"] = dict(payload) if isinstance(payload, dict) else {}

    return normalized


def _normalize_error(error: Any) -> dict[str, str] | None:
    if not isinstance(error, dict):
        return None

    error_type = error.get("type")
    message = error.get("message")
    if not isinstance(error_type, str) or not error_type:
        return None
    if not isinstance(message, str) or not message:
        return None

    return {
        "type": error_type,
        "message": message,
    }
