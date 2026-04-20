from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trader_shawn.domain.enums import DecisionAction


REQUIRED_APPROVAL_FIELDS = (
    "ticker",
    "strategy",
    "expiry",
    "short_strike",
    "long_strike",
    "limit_credit",
    "confidence",
    "reason",
    "risk_note",
)


@dataclass(slots=True)
class ParsedDecision:
    action: DecisionAction
    ticker: str | None = None
    strategy: str | None = None
    expiry: str | None = None
    short_strike: float | None = None
    long_strike: float | None = None
    limit_credit: float | None = None
    confidence: float | None = None
    reason: str = ""
    risk_note: str | None = None
    secondary_payload: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)


def _require_str(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing or invalid field: {field_name}")
    return value


def _require_number(payload: dict[str, Any], field_name: str) -> float:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"missing or invalid field: {field_name}")
    return float(value)


def _optional_number(payload: dict[str, Any], field_name: str) -> float | None:
    if field_name not in payload or payload[field_name] is None:
        return None
    return _require_number(payload, field_name)


def parse_decision(payload: dict[str, Any]) -> ParsedDecision:
    if not isinstance(payload, dict):
        raise ValueError("decision payload must be a dictionary")

    try:
        action = DecisionAction(payload["action"])
    except KeyError as exc:
        raise ValueError("missing or invalid field: action") from exc
    except ValueError as exc:
        raise ValueError("missing or invalid field: action") from exc

    if action is DecisionAction.APPROVE:
        for field_name in REQUIRED_APPROVAL_FIELDS:
            if field_name not in payload:
                raise ValueError(f"missing or invalid field: {field_name}")

        return ParsedDecision(
            action=action,
            ticker=_require_str(payload, "ticker"),
            strategy=_require_str(payload, "strategy"),
            expiry=_require_str(payload, "expiry"),
            short_strike=_require_number(payload, "short_strike"),
            long_strike=_require_number(payload, "long_strike"),
            limit_credit=_require_number(payload, "limit_credit"),
            confidence=_require_number(payload, "confidence"),
            reason=_require_str(payload, "reason"),
            risk_note=_require_str(payload, "risk_note"),
            raw_payload=dict(payload),
        )

    return ParsedDecision(
        action=action,
        ticker=payload.get("ticker"),
        strategy=payload.get("strategy"),
        expiry=payload.get("expiry"),
        short_strike=_optional_number(payload, "short_strike"),
        long_strike=_optional_number(payload, "long_strike"),
        limit_credit=_optional_number(payload, "limit_credit"),
        confidence=_optional_number(payload, "confidence"),
        reason=_require_str(payload, "reason"),
        risk_note=payload.get("risk_note"),
        raw_payload=dict(payload),
    )
