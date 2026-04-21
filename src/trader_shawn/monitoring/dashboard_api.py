from __future__ import annotations

from pathlib import Path
from typing import Any

from trader_shawn.domain.models import _json_safe_payload
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
    snapshot_path = Path(path)
    try:
        loaded = StateStore(snapshot_path).load()
    except StateStoreError as exc:
        return _snapshot_shape(
            status="error",
            error={
                "type": type(exc).__name__,
                "message": str(exc),
            },
        )
    except OSError as exc:
        return _snapshot_shape(
            status="error",
            error={
                "type": "OSError",
                "message": str(snapshot_path),
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
    for key in (
        "status",
        "reason",
        "action",
        "broker",
        "broker_status",
        "error_type",
        "message",
        "ticker",
        "position_id",
        "exit_reason",
    ):
        value = last_cycle.get(key)
        if isinstance(value, str) and value:
            normalized[key] = value

    managed_count = last_cycle.get("managed_count")
    if isinstance(managed_count, int) and not isinstance(managed_count, bool):
        normalized["managed_count"] = managed_count

    order_id = last_cycle.get("order_id")
    if isinstance(order_id, int) and not isinstance(order_id, bool):
        normalized["order_id"] = order_id

    fingerprints = last_cycle.get("fingerprints")
    if isinstance(fingerprints, list):
        normalized["fingerprints"] = [
            item for item in fingerprints if isinstance(item, str) and item
        ]

    for key in ("payload", "order", "contract"):
        if key not in last_cycle:
            continue
        value = last_cycle.get(key)
        if not isinstance(value, dict):
            normalized[key] = {}
            continue
        try:
            normalized[key] = _json_safe_payload(value, path=key)
        except TypeError:
            normalized[key] = {}

    audit_error = _normalize_error(last_cycle.get("audit_error"))
    if audit_error is not None:
        normalized["audit_error"] = audit_error

    legs = last_cycle.get("legs")
    if isinstance(legs, list):
        try:
            normalized["legs"] = _json_safe_payload(legs, path="legs")
        except TypeError:
            normalized["legs"] = []

    manual_intervention_required = last_cycle.get("manual_intervention_required")
    if isinstance(manual_intervention_required, bool):
        normalized["manual_intervention_required"] = manual_intervention_required

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
