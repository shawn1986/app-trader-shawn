import json
from subprocess import TimeoutExpired

from trader_shawn.ai.base import AiProviderError
from trader_shawn.ai.service import AiDecisionService


class StubProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def request(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        return self.payload


class FailingProvider:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.prompts: list[str] = []

    def request(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        raise self.error


class NamedFailingProvider:
    def __init__(self, provider_name: str, error: Exception) -> None:
        self.provider_name = provider_name
        self.error = error
        self.prompts: list[str] = []

    def request(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        raise self.error


def test_ai_service_returns_primary_decision_and_secondary_note() -> None:
    primary = StubProvider(
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
    )
    secondary = StubProvider({"action": "reject", "reason": "too concentrated"})
    service = AiDecisionService(
        primary=primary,
        secondary=secondary,
    )

    context = {"ticker": "AMD", "candidates": [], "window": {"start": "09:30"}}
    decision = service.decide(context)

    assert decision.action == "approve"
    assert decision.secondary_payload["reason"] == "too concentrated"
    assert primary.prompts == secondary.prompts == [json.dumps(context, sort_keys=True)]


def test_ai_service_preserves_secondary_failure_metadata() -> None:
    secondary = FailingProvider(
        AiProviderError("codex", "empty stdout", stdout="", stderr="timeout")
    )
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
        secondary=secondary,
    )

    decision = service.decide({"ticker": "AMD", "candidates": []})

    assert decision.action == "approve"
    assert decision.secondary_payload == {
        "provider": "codex",
        "failure_type": "AiProviderError",
        "reason": "empty stdout",
        "stdout": "",
        "stderr": "timeout",
    }


def test_ai_service_preserves_secondary_runtime_failure_provider_identity() -> None:
    secondary = NamedFailingProvider(
        "codex",
        TimeoutExpired(cmd=["codex", "exec"], timeout=10, output="", stderr="timeout"),
    )
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
        secondary=secondary,
    )

    decision = service.decide({"ticker": "AMD", "candidates": []})

    assert decision.action == "approve"
    assert decision.secondary_payload == {
        "provider": "codex",
        "failure_type": "AiProviderError",
        "reason": "provider command timed out",
        "stdout": "",
        "stderr": "timeout",
    }
