from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from trader_shawn.war_room.models import (
    AccountRail,
    BrokerCommandStatus,
    Freshness,
    HotPosition,
    ThreatLevel,
    ThreatRail,
)

BROKER_STALE_AFTER = timedelta(seconds=30)


def build_war_room_snapshot(
    *,
    dashboard_state: dict[str, Any] | None,
    account_snapshot: dict[str, Any] | None,
    managed_positions: list[dict[str, Any]] | None,
    position_events: list[dict[str, Any]] | None,
    broker_health: dict[str, Any] | None,
    mission_log_events: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = _normalize_datetime(now or datetime.now(UTC))
    normalized_dashboard_state = _as_dict(dashboard_state)
    last_cycle = _as_dict(normalized_dashboard_state.get("last_cycle"))
    normalized_positions = _as_dict_list(managed_positions)
    normalized_events = _as_dict_list(position_events)
    normalized_mission_log_events = (
        _as_dict_list(mission_log_events)
        if mission_log_events is not None
        else normalized_events
    )

    threat_rail = ThreatRail(
        cycle_status=_as_non_empty_str(last_cycle.get("status")),
        reason=_as_non_empty_str(last_cycle.get("reason")),
        manual_intervention_required=bool(last_cycle.get("manual_intervention_required")),
        fingerprints=_as_str_list(last_cycle.get("fingerprints")),
    )
    broker_status = _build_broker_command_status(broker_health, now=current_time)
    hot_positions = _build_hot_positions(
        managed_positions=normalized_positions,
        position_events=normalized_events,
    )
    account_rail = _build_account_rail(_as_dict(account_snapshot))
    threat_level = _derive_threat_level(
        threat_rail=threat_rail,
        broker_status=broker_status,
        hot_positions=hot_positions,
    )
    return {
        "generated_at": current_time.isoformat(),
        "threat_level": threat_level,
        "command_status": {
            "broker": broker_status.to_dict(),
            "dashboard_status": _as_non_empty_str(normalized_dashboard_state.get("status")),
            "last_cycle": dict(last_cycle),
        },
        "risk_deck": _build_risk_deck(
            account_rail=account_rail,
            active_managed_positions=len(normalized_positions),
        ),
        "hot_positions": [position.to_dict() for position in hot_positions],
        "mission_log": _build_mission_log(normalized_mission_log_events),
        "threat_rail": threat_rail.to_dict(),
        "account_rail": account_rail.to_dict(),
    }


def _build_account_rail(account_snapshot: dict[str, Any]) -> AccountRail:
    return AccountRail(
        net_liquidation=_as_float(account_snapshot.get("net_liquidation")),
        unrealized_pnl=_as_float(account_snapshot.get("unrealized_pnl")),
        open_risk=_as_float(account_snapshot.get("open_risk")),
        new_positions_today=_as_int(account_snapshot.get("new_positions_today")),
    )


def _build_risk_deck(
    *,
    account_rail: AccountRail,
    active_managed_positions: int,
) -> dict[str, Any]:
    return {
        "net_liquidation": account_rail.net_liquidation,
        "unrealized_pnl": account_rail.unrealized_pnl,
        "open_risk": account_rail.open_risk,
        "new_positions_today": account_rail.new_positions_today,
        "active_managed_positions": active_managed_positions,
    }


def _build_mission_log(position_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mission_log: list[dict[str, Any]] = []
    for event in position_events:
        payload_json = event.get("payload_json")
        payload = payload_json if isinstance(payload_json, dict) else _as_dict(event.get("payload"))
        mission_log.append(
            {
                "position_id": _as_non_empty_str(event.get("position_id")),
                "event_type": _as_non_empty_str(event.get("event_type")),
                "payload": payload,
                "created_at": _event_iso(event),
            }
        )
    return mission_log


def _build_broker_command_status(
    broker_health: dict[str, Any] | None,
    *,
    now: datetime,
) -> BrokerCommandStatus:
    data = broker_health or {}
    checked_at = _parse_datetime(data.get("checked_at"))
    freshness = _derive_freshness(checked_at=checked_at, now=now)
    latency_ms = data.get("latency_ms")
    return BrokerCommandStatus(
        state="healthy" if bool(data.get("connected")) else "degraded",
        freshness=freshness,
        checked_at=checked_at.isoformat() if checked_at is not None else None,
        latency_ms=latency_ms if isinstance(latency_ms, int) else None,
        message=_as_non_empty_str(data.get("message")),
    )


def _derive_freshness(*, checked_at: datetime | None, now: datetime) -> Freshness:
    if checked_at is None:
        return "unknown"
    normalized_now = _normalize_datetime(now)
    normalized_checked_at = _normalize_datetime(checked_at)
    if normalized_now - normalized_checked_at > BROKER_STALE_AFTER:
        return "stale"
    return "fresh"


def _build_hot_positions(
    *,
    managed_positions: list[dict[str, Any]],
    position_events: list[dict[str, Any]],
) -> list[HotPosition]:
    latest_events: dict[str, dict[str, Any]] = {}
    for event in position_events:
        if not isinstance(event, dict):
            continue
        position_id = _as_non_empty_str(event.get("position_id"))
        if not position_id:
            continue
        latest = latest_events.get(position_id)
        if latest is None:
            latest_events[position_id] = event
            continue
        if _event_sort_key(event) >= _event_sort_key(latest):
            latest_events[position_id] = event

    hot_positions: list[HotPosition] = []
    for position in managed_positions:
        if not isinstance(position, dict):
            continue
        position_id = _as_non_empty_str(position.get("position_id"))
        event = latest_events.get(position_id)
        hot_positions.append(
            HotPosition(
                position_id=position_id,
                ticker=_as_non_empty_str(position.get("ticker")),
                status=_as_non_empty_str(position.get("status")),
                expiry=_as_non_empty_str(position.get("expiry")),
                last_known_debit=_as_float_or_none(position.get("last_known_debit")),
                latest_event_type=_as_non_empty_str(event.get("event_type")) if event else None,
                latest_event_at=_event_iso(event) if event else None,
            )
        )

    hot_positions.sort(key=_hot_position_sort_key)
    return hot_positions


def _derive_threat_level(
    *,
    threat_rail: ThreatRail,
    broker_status: BrokerCommandStatus,
    hot_positions: list[HotPosition],
) -> ThreatLevel:
    if threat_rail.manual_intervention_required:
        return "critical"
    if broker_status.freshness == "stale" or broker_status.state == "degraded":
        return "warning"
    if any(_is_uncertain_event(position.latest_event_type) for position in hot_positions):
        return "warning"
    return "nominal"


def _hot_position_sort_key(position: HotPosition) -> tuple[int, int, str]:
    has_uncertain_event = _is_uncertain_event(position.latest_event_type)
    is_closing = position.status == "closing"
    return (
        0 if has_uncertain_event else 1,
        0 if is_closing else 1,
        position.ticker,
    )


def _is_uncertain_event(event_type: str | None) -> bool:
    return bool(event_type) and "uncertain" in event_type


def _event_sort_key(event: dict[str, Any]) -> tuple[datetime, str]:
    parsed = _parse_datetime(event.get("created_at")) or datetime.min.replace(tzinfo=UTC)
    return (parsed, _as_non_empty_str(event.get("event_type")))


def _event_iso(event: dict[str, Any]) -> str | None:
    parsed = _parse_datetime(event.get("created_at"))
    if parsed is None:
        return None
    return parsed.isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _normalize_datetime(parsed)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_non_empty_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _as_float(value: Any) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _as_float_or_none(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]
