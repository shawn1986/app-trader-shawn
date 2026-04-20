import pytest

from trader_shawn.ai.decision_parser import parse_decision
from trader_shawn.domain.enums import DecisionAction


def _approval_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": "approve",
        "ticker": "AMD",
        "strategy": "bull_put_credit_spread",
        "expiry": "2026-04-30",
        "short_strike": 160.0,
        "long_strike": 155.0,
        "limit_credit": 1.05,
        "confidence": 0.72,
        "reason": "within limits",
        "risk_note": "size acceptable",
    }
    payload.update(overrides)
    return payload


def test_parse_decision_rejects_boolean_optional_numeric_for_non_approve() -> None:
    with pytest.raises(ValueError, match="missing or invalid field: confidence"):
        parse_decision(
            {
                "action": "reject",
                "reason": "too concentrated",
                "confidence": True,
            }
        )


def test_parse_decision_rejects_invalid_optional_numeric_for_non_approve() -> None:
    with pytest.raises(ValueError, match="missing or invalid field: short_strike"):
        parse_decision(
            {
                "action": "hold",
                "reason": "need more confirmation",
                "short_strike": "160",
            }
        )


def test_parse_decision_accepts_non_approve_without_optional_numerics() -> None:
    decision = parse_decision({"action": "reject", "reason": "too concentrated"})

    assert decision.action is DecisionAction.REJECT
    assert decision.short_strike is None
    assert decision.confidence is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"confidence": -0.01}, "confidence"),
        ({"confidence": 1.01}, "confidence"),
        ({"confidence": float("nan")}, "confidence"),
        ({"confidence": float("inf")}, "confidence"),
        ({"short_strike": float("inf")}, "short_strike"),
        ({"limit_credit": 0.0}, "limit_credit"),
        ({"limit_credit": -0.01}, "limit_credit"),
        ({"short_strike": 155.0, "long_strike": 160.0}, "short_strike"),
        ({"short_strike": 160.0, "long_strike": 160.0}, "short_strike"),
    ],
)
def test_parse_decision_rejects_invalid_approval_invariants(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_decision(_approval_payload(**overrides))
