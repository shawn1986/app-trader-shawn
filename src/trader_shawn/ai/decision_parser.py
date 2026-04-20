from __future__ import annotations

from dataclasses import dataclass, field
import math
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


def _require_finite_approval_number(value: float, field_name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"invalid approval field: {field_name} must be finite")
    return value


def _validate_approval_fields(
    strategy: str,
    short_strike: float,
    long_strike: float,
    limit_credit: float,
    confidence: float,
) -> None:
    short_strike = _require_finite_approval_number(short_strike, "short_strike")
    long_strike = _require_finite_approval_number(long_strike, "long_strike")
    limit_credit = _require_finite_approval_number(limit_credit, "limit_credit")
    confidence = _require_finite_approval_number(confidence, "confidence")

    normalized_strategy = strategy.lower()
    if normalized_strategy == "bull_put_credit_spread" and short_strike <= long_strike:
        raise ValueError("invalid approval field: short_strike must be greater than long_strike")
    if normalized_strategy == "bear_call_credit_spread" and short_strike >= long_strike:
        raise ValueError("invalid approval field: short_strike must be less than long_strike")
    if limit_credit <= 0:
        raise ValueError("invalid approval field: limit_credit must be greater than 0")
    if not 0 <= confidence <= 1:
        raise ValueError("invalid approval field: confidence must be between 0 and 1")


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

        strategy = _require_str(payload, "strategy")
        short_strike = _require_number(payload, "short_strike")
        long_strike = _require_number(payload, "long_strike")
        limit_credit = _require_number(payload, "limit_credit")
        confidence = _require_number(payload, "confidence")
        _validate_approval_fields(
            strategy,
            short_strike,
            long_strike,
            limit_credit,
            confidence,
        )

        return ParsedDecision(
            action=action,
            ticker=_require_str(payload, "ticker"),
            strategy=strategy,
            expiry=_require_str(payload, "expiry"),
            short_strike=short_strike,
            long_strike=long_strike,
            limit_credit=limit_credit,
            confidence=confidence,
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
