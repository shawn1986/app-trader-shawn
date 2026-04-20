import pytest

from trader_shawn.ai.decision_parser import parse_decision
from trader_shawn.domain.enums import DecisionAction


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
