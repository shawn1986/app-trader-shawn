import json
from datetime import UTC, datetime
from subprocess import TimeoutExpired

from trader_shawn.ai.base import AiProviderError
from trader_shawn.ai.service import AiDecisionService
from trader_shawn.domain.enums import PositionSide
from trader_shawn.domain.models import AccountSnapshot, CandidateSpread, OptionQuote


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


def test_ai_service_serializes_nested_domain_context_objects() -> None:
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
    service = AiDecisionService(primary=primary)
    as_of = datetime(2026, 4, 20, 9, 30, tzinfo=UTC)
    short_leg = OptionQuote(
        symbol="AMD",
        expiry="2026-04-30",
        strike=160,
        right="put",
        bid=1.7,
        ask=1.8,
        delta=-0.22,
        volume=100,
        open_interest=200,
    )
    long_leg = OptionQuote(
        symbol="AMD",
        expiry="2026-04-30",
        strike=155,
        right="put",
        bid=0.65,
        ask=0.75,
        delta=-0.12,
        volume=100,
        open_interest=200,
    )
    context = {
        "ticker": "AMD",
        "account": AccountSnapshot(
            account_id="acct-1",
            net_liquidation=25_000,
            updated_at=as_of,
        ),
        "candidate": CandidateSpread(
            ticker="AMD",
            strategy="bull_put_credit_spread",
            short_leg=short_leg,
            long_leg=long_leg,
            dte=10,
            credit=1.05,
            max_loss=3.95,
            width=5.0,
            expiry="2026-04-30",
            short_delta=0.22,
            pop=0.78,
            bid_ask_ratio=0.1,
        ),
        "side": PositionSide.LONG,
        "generated_at": as_of,
    }

    decision = service.decide(context)
    prompt_payload = json.loads(primary.prompts[0])

    assert decision.action == "approve"
    assert prompt_payload == {
        "account": {
            "account_id": "acct-1",
            "buying_power": 0.0,
            "cash": 0.0,
            "excess_liquidity": 0.0,
            "net_liquidation": 25_000,
            "new_positions_today": 0,
            "open_risk": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "updated_at": "2026-04-20T09:30:00+00:00",
        },
        "candidate": {
            "bid_ask_ratio": 0.1,
            "credit": 1.05,
            "dte": 10,
            "expiry": "2026-04-30",
            "long_leg": {
                "ask": 0.75,
                "bid": 0.65,
                "delta": -0.12,
                "expiry": "2026-04-30",
                "last": None,
                "mark": None,
                "open_interest": 200,
                "right": "P",
                "strike": 155,
                "symbol": "AMD",
                "volume": 100,
            },
            "long_strike": 155,
            "max_loss": 3.95,
            "pop": 0.78,
            "short_delta": 0.22,
            "short_leg": {
                "ask": 1.8,
                "bid": 1.7,
                "delta": -0.22,
                "expiry": "2026-04-30",
                "last": None,
                "mark": None,
                "open_interest": 200,
                "right": "P",
                "strike": 160,
                "symbol": "AMD",
                "volume": 100,
            },
            "short_strike": 160,
            "strategy": "bull_put_credit_spread",
            "ticker": "AMD",
            "width": 5.0,
        },
        "generated_at": "2026-04-20T09:30:00+00:00",
        "side": "long",
        "ticker": "AMD",
    }
