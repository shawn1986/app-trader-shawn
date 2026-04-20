from trader_shawn.ai.service import AiDecisionService


class StubProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def request(self, prompt: str) -> dict:
        return self.payload


def test_ai_service_returns_primary_decision_and_secondary_note() -> None:
    service = AiDecisionService(
        primary=StubProvider(
            {
                "action": "approve",
                "ticker": "AMD",
                "strategy": "bull_put_credit_spread",
                "expiry": "2026-04-30",
                "short_strike": 160,
                "long_strike": 155,
                "limit_credit": 1.05,
                "confidence": 0.72,
                "reason": "primary",
                "risk_note": "ok",
            }
        ),
        secondary=StubProvider({"action": "reject", "reason": "too concentrated"}),
    )

    decision = service.decide({"ticker": "AMD", "candidates": []})

    assert decision.action == "approve"
    assert decision.secondary_payload["reason"] == "too concentrated"
