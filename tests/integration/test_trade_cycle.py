from __future__ import annotations

from trader_shawn.app import run_trade_cycle
from trader_shawn.domain.models import AccountSnapshot, CandidateSpread


class StubDecisionService:
    def __init__(self, *, action: str = "approve") -> None:
        self._action = action
        self.calls: list[dict] = []

    def decide(self, context: dict):
        self.calls.append(context)

        class Decision:
            action = self._action
            ticker = "AMD"
            strategy = "bull_put_credit_spread"
            expiry = "2026-04-30"
            short_strike = 160
            long_strike = 155
            limit_credit = 1.05
            reason = "ok"
            secondary_payload = {"reason": "secondary"}

        return Decision()


class StubExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[object, float]] = []

    def submit_limit_combo(self, payload: object, *, limit_price: float) -> dict:
        self.calls.append((payload, limit_price))
        return {"status": "submitted", "payload": {"symbol": payload.ticker}}


class StubRiskGuard:
    def __init__(self, *, allowed: bool, reason: str = "ok") -> None:
        self.allowed = allowed
        self.reason = reason
        self.calls: list[tuple[CandidateSpread, AccountSnapshot, int]] = []

    def evaluate(
        self,
        spread: CandidateSpread,
        account: AccountSnapshot,
        open_symbol_count: int,
    ):
        self.calls.append((spread, account, open_symbol_count))

        class GuardResult:
            allowed = self.allowed
            reason = self.reason

        return GuardResult()


def _spread() -> CandidateSpread:
    return CandidateSpread(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        dte=10,
        short_strike=160,
        long_strike=155,
        width=5,
        credit=1.0,
        max_loss=400,
        short_delta=0.20,
        pop=0.80,
        bid_ask_ratio=0.08,
    )


def _account() -> AccountSnapshot:
    return AccountSnapshot(
        net_liq=50_000,
        realized_pnl=0,
        unrealized_pnl=0,
        open_risk=0,
        new_positions_today=0,
    )


def test_run_trade_cycle_returns_no_candidates_without_decision_or_submission() -> None:
    decision_service = StubDecisionService()
    executor = StubExecutor()

    result = run_trade_cycle(
        candidates=[],
        account=_account(),
        decision_service=decision_service,
        executor=executor,
    )

    assert result == {"status": "no_candidates"}
    assert decision_service.calls == []
    assert executor.calls == []


def test_run_trade_cycle_stops_when_decision_is_not_approve() -> None:
    decision_service = StubDecisionService(action="reject")
    executor = StubExecutor()

    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=decision_service,
        executor=executor,
    )

    assert result["status"] == "decision_rejected"
    assert result["reason"] == "ok"
    assert executor.calls == []


def test_run_trade_cycle_stops_when_risk_guard_rejects_candidate() -> None:
    executor = StubExecutor()
    risk_guard = StubRiskGuard(allowed=False, reason="max_open_risk_pct")

    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=StubDecisionService(),
        executor=executor,
        risk_guard=risk_guard,
    )

    assert result == {"status": "risk_rejected", "reason": "max_open_risk_pct"}
    assert len(risk_guard.calls) == 1
    assert executor.calls == []


def test_run_trade_cycle_submits_order_when_candidate_and_risk_pass() -> None:
    spread = _spread()

    result = run_trade_cycle(
        candidates=[spread],
        account=_account(),
        decision_service=StubDecisionService(),
        executor=StubExecutor(),
        risk_guard=StubRiskGuard(allowed=True),
    )

    assert result["status"] == "submitted"
    assert result["payload"]["symbol"] == "AMD"
