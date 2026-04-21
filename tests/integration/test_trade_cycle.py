from __future__ import annotations

import math
import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

import trader_shawn.app as app_module
from trader_shawn.app import cli, run_trade_cycle
from trader_shawn.domain.models import AccountSnapshot, CandidateSpread
from trader_shawn.execution.ibkr_executor import IbkrExecutor
from trader_shawn.monitoring.dashboard_api import (
    build_dashboard_snapshot,
    read_dashboard_snapshot,
    update_dashboard_state,
)


class StubDecisionService:
    def __init__(
        self,
        *,
        action: str = "approve",
        ticker: str = "AMD",
        expiry: str = "2026-04-30",
        short_strike: float = 160,
        long_strike: float = 155,
        limit_credit: float = 1.05,
        error: Exception | None = None,
    ) -> None:
        self._action = action
        self._ticker = ticker
        self._expiry = expiry
        self._short_strike = short_strike
        self._long_strike = long_strike
        self._limit_credit = limit_credit
        self._error = error
        self.calls: list[dict] = []

    def decide(self, context: dict):
        self.calls.append(context)
        if self._error is not None:
            raise self._error

        class Decision:
            action = self._action
            ticker = self._ticker
            strategy = "bull_put_credit_spread"
            expiry = self._expiry
            short_strike = self._short_strike
            long_strike = self._long_strike
            limit_credit = self._limit_credit
            reason = "ok"
            secondary_payload = {"reason": "secondary"}

        return Decision()


class StubExecutor:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.open_calls: list[tuple[object, float, int]] = []
        self.close_calls: list[tuple[object, float]] = []
        self._error = error

    def submit_open_credit_spread(
        self,
        payload: object,
        *,
        limit_credit: float,
        quantity: int = 1,
    ) -> dict:
        if self._error is not None:
            raise self._error
        self.open_calls.append((payload, limit_credit, quantity))
        return {
            "status": "submitted",
            "payload": {
                "symbol": payload.ticker,
                "expiry": payload.expiry,
                "short_strike": payload.short_strike,
                "long_strike": payload.long_strike,
            },
        }

    def submit_limit_combo(self, payload: object, *, limit_price: float) -> dict:
        if self._error is not None:
            raise self._error
        self.close_calls.append((payload, limit_price))
        return {
            "status": "submitted",
            "payload": {
                "symbol": payload.ticker,
                "expiry": payload.expiry,
                "short_strike": payload.short_strike,
                "long_strike": payload.long_strike,
            },
        }


class IbkrShapeExecutor(StubExecutor):
    def submit_open_credit_spread(
        self,
        payload: object,
        *,
        limit_credit: float,
        quantity: int = 1,
    ) -> dict:
        if self._error is not None:
            raise self._error
        self.open_calls.append((payload, limit_credit, quantity))
        return {
            "status": "submitted",
            "broker": "ibkr",
            "order_id": 321,
            "order": {
                "action": "SELL",
                "orderType": "LMT",
                "totalQuantity": quantity,
                "lmtPrice": limit_credit,
                "transmit": True,
            },
            "contract": {
                "symbol": payload.ticker,
                "secType": "BAG",
                "currency": "USD",
                "exchange": "SMART",
            },
            "legs": [
                {
                    "con_id": 70160,
                    "action": "SELL",
                    "ratio": 1,
                    "exchange": "SMART",
                    "right": "P",
                    "strike": 160.0,
                    "expiry": payload.expiry,
                },
                {
                    "con_id": 70155,
                    "action": "BUY",
                    "ratio": 1,
                    "exchange": "SMART",
                    "right": "P",
                    "strike": 155.0,
                    "expiry": payload.expiry,
                },
            ],
            "broker_status": "PendingSubmit",
        }


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


class PayloadDecisionService:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def decide(self, _: dict):
        return type("Decision", (), self.payload)()


class FakeScanner:
    def __init__(self, candidates: list[CandidateSpread]) -> None:
        self.candidates = list(candidates)
        self.calls: list[list[str]] = []

    def scan_candidates(self, symbols: list[str]) -> list[CandidateSpread]:
        self.calls.append(list(symbols))
        return list(self.candidates)


class FakeAccountService:
    def __init__(self, snapshot: AccountSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def fetch_account_snapshot(self) -> AccountSnapshot:
        self.calls += 1
        return self.snapshot


class FakePositionService:
    def __init__(self, *, open_symbol_count: int = 0) -> None:
        self.open_symbol_count = open_symbol_count
        self.count_calls: list[str] = []

    def count_open_option_positions(self, ticker: str) -> int:
        self.count_calls.append(ticker)
        return self.open_symbol_count


class _FakeOption:
    def __init__(
        self,
        *,
        symbol: str,
        lastTradeDateOrContractMonth: str,
        strike: float,
        right: str,
        exchange: str,
        currency: str,
    ) -> None:
        self.symbol = symbol
        self.lastTradeDateOrContractMonth = lastTradeDateOrContractMonth
        self.strike = strike
        self.right = right
        self.exchange = exchange
        self.currency = currency
        self.conId = 0


class _FakeComboLeg:
    def __init__(self, *, conId: int, ratio: int, action: str, exchange: str) -> None:
        self.conId = conId
        self.ratio = ratio
        self.action = action
        self.exchange = exchange


class _FakeContract:
    def __init__(
        self,
        *,
        symbol: str,
        secType: str,
        currency: str,
        exchange: str,
        comboLegs: list[_FakeComboLeg],
    ) -> None:
        self.symbol = symbol
        self.secType = secType
        self.currency = currency
        self.exchange = exchange
        self.comboLegs = comboLegs


class _FakeLimitOrder:
    def __init__(
        self,
        action: str,
        totalQuantity: int,
        lmtPrice: float,
        *,
        transmit: bool,
    ) -> None:
        self.action = action
        self.totalQuantity = totalQuantity
        self.lmtPrice = lmtPrice
        self.transmit = transmit
        self.orderType = "LMT"
        self.orderId = 404


class _FakeIbModule:
    Option = _FakeOption
    ComboLeg = _FakeComboLeg
    Contract = _FakeContract
    LimitOrder = _FakeLimitOrder


class _FakeIbClient:
    def qualifyContracts(self, *contracts: _FakeOption) -> list[_FakeOption]:
        for contract in contracts:
            contract.conId = 50000 + int(contract.strike)
        return list(contracts)

    def placeOrder(self, contract: _FakeContract, order: _FakeLimitOrder):
        return type(
            "Trade",
            (),
            {
                "contract": contract,
                "order": order,
                "orderStatus": type("OrderStatus", (), {"status": "PendingSubmit"})(),
            },
        )()


def _spread(
    *,
    ticker: str = "AMD",
    expiry: str = "2026-04-30",
    short_strike: float = 160,
    long_strike: float = 155,
    credit: float = 1.0,
    max_loss: float = 400,
) -> CandidateSpread:
    return CandidateSpread(
        ticker=ticker,
        strategy="bull_put_credit_spread",
        expiry=expiry,
        dte=10,
        short_strike=short_strike,
        long_strike=long_strike,
        width=5,
        credit=credit,
        max_loss=max_loss,
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


def _settings(*, symbols: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        mode="paper",
        live_enabled=False,
        symbols=symbols or ["SPY", "QQQ", "AMD"],
        risk=SimpleNamespace(
            max_risk_per_trade_pct=0.02,
            max_daily_loss_pct=0.04,
            max_open_risk_pct=0.2,
            max_spreads_per_symbol=2,
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        providers=SimpleNamespace(
            provider_mode="claude_primary",
            primary_provider="claude_cli",
            secondary_provider="codex",
            provider_timeout_seconds=15,
            secondary_timeout_seconds=10,
        ),
        ibkr=SimpleNamespace(host="127.0.0.1", port=4002),
    )


def _runtime(
    *,
    scanner: FakeScanner | None = None,
    decision_service: object | None = None,
    account_service: FakeAccountService | None = None,
    position_service: FakePositionService | None = None,
    risk_guard: object | None = None,
    executor: object | None = None,
    dashboard_state_path: Path | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        settings=_settings(),
        config_dir=(Path.cwd() / "config").resolve(),
        scanner=scanner,
        decision_service=decision_service,
        account_service=account_service,
        position_service=position_service,
        risk_guard=risk_guard,
        executor=executor,
        dashboard_state_path=dashboard_state_path,
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
    assert executor.open_calls == []
    assert executor.close_calls == []


def test_cli_is_invokable_via_click_runner() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "trade-cycle" in result.output
    assert "dashboard" in result.output


def test_trade_cycle_command_executes_workflow_and_updates_dashboard(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    candidate = _spread()
    scanner = FakeScanner([candidate])
    account_service = FakeAccountService(_account())
    position_service = FakePositionService(open_symbol_count=1)
    executor = StubExecutor()
    decision_service = StubDecisionService(limit_credit=1.10)
    risk_guard = StubRiskGuard(allowed=True)
    state_path = tmp_path / "dashboard.json"
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=scanner,
            decision_service=decision_service,
            account_service=account_service,
            position_service=position_service,
            risk_guard=risk_guard,
            executor=executor,
            dashboard_state_path=state_path,
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade-cycle"])

    assert result.exit_code == 0
    payload = json.loads(result.output)

    assert payload["status"] == "submitted"
    assert payload["command"] == "trade-cycle"
    assert payload["payload"]["symbol"] == "AMD"
    assert scanner.calls == [["SPY", "QQQ", "AMD"]]
    assert account_service.calls == 1
    assert position_service.count_calls == ["AMD"]
    assert executor.open_calls[0][0] is candidate
    assert read_dashboard_snapshot(state_path) == {
        "status": "updated",
        "last_cycle": {
            "status": "submitted",
            "payload": {
                "symbol": "AMD",
                "expiry": "2026-04-30",
                "short_strike": 160,
                "long_strike": 155,
            },
        },
        "error": None,
    }


def test_trade_cycle_command_preserves_ibkr_submission_shape_in_dashboard(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    candidate = _spread()
    state_path = tmp_path / "dashboard.json"
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=FakeScanner([candidate]),
            decision_service=StubDecisionService(limit_credit=1.10),
            account_service=FakeAccountService(_account()),
            position_service=FakePositionService(open_symbol_count=1),
            risk_guard=StubRiskGuard(allowed=True),
            executor=IbkrShapeExecutor(),
            dashboard_state_path=state_path,
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade-cycle"])

    assert result.exit_code == 0
    assert read_dashboard_snapshot(state_path) == {
        "status": "updated",
        "last_cycle": {
            "status": "submitted",
            "broker": "ibkr",
            "order_id": 321,
            "order": {
                "action": "SELL",
                "orderType": "LMT",
                "totalQuantity": 1,
                "lmtPrice": 1.1,
                "transmit": True,
            },
            "contract": {
                "symbol": "AMD",
                "secType": "BAG",
                "currency": "USD",
                "exchange": "SMART",
            },
            "legs": [
                {
                    "con_id": 70160,
                    "action": "SELL",
                    "ratio": 1,
                    "exchange": "SMART",
                    "right": "P",
                    "strike": 160.0,
                    "expiry": "2026-04-30",
                },
                {
                    "con_id": 70155,
                    "action": "BUY",
                    "ratio": 1,
                    "exchange": "SMART",
                    "right": "P",
                    "strike": 155.0,
                    "expiry": "2026-04-30",
                },
            ],
            "broker_status": "PendingSubmit",
        },
        "error": None,
    }


def test_trade_cycle_command_reports_dashboard_update_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    candidate = _spread()
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=FakeScanner([candidate]),
            decision_service=StubDecisionService(limit_credit=1.10),
            account_service=FakeAccountService(_account()),
            position_service=FakePositionService(open_symbol_count=1),
            risk_guard=StubRiskGuard(allowed=True),
            executor=StubExecutor(),
            dashboard_state_path=tmp_path / "dashboard.json",
        ),
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "update_dashboard_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("dashboard write failed")),
        raising=False,
    )

    result = runner.invoke(cli, ["trade-cycle"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "trade-cycle",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "dashboard_error": {
            "type": "OSError",
            "message": "dashboard write failed",
        },
        "live_enabled": False,
        "mode": "paper",
        "payload": {
            "expiry": "2026-04-30",
            "long_strike": 155,
            "short_strike": 160,
            "symbol": "AMD",
        },
        "status": "submitted",
    }


def test_trade_cycle_command_preserves_ibkr_submission_when_dashboard_update_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    candidate = _spread()
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=FakeScanner([candidate]),
            decision_service=StubDecisionService(limit_credit=1.10),
            account_service=FakeAccountService(_account()),
            position_service=FakePositionService(open_symbol_count=1),
            risk_guard=StubRiskGuard(allowed=True),
            executor=IbkrShapeExecutor(),
            dashboard_state_path=tmp_path / "dashboard.json",
        ),
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "update_dashboard_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("dashboard write failed")),
        raising=False,
    )

    result = runner.invoke(cli, ["trade-cycle"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "trade-cycle",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "dashboard_error": {
            "type": "OSError",
            "message": "dashboard write failed",
        },
        "live_enabled": False,
        "mode": "paper",
        "status": "submitted",
        "broker": "ibkr",
        "order_id": 321,
        "order": {
            "action": "SELL",
            "orderType": "LMT",
            "totalQuantity": 1,
            "lmtPrice": 1.1,
            "transmit": True,
        },
        "contract": {
            "symbol": "AMD",
            "secType": "BAG",
            "currency": "USD",
            "exchange": "SMART",
        },
        "legs": [
            {
                "con_id": 70160,
                "action": "SELL",
                "ratio": 1,
                "exchange": "SMART",
                "right": "P",
                "strike": 160.0,
                "expiry": "2026-04-30",
            },
            {
                "con_id": 70155,
                "action": "BUY",
                "ratio": 1,
                "exchange": "SMART",
                "right": "P",
                "strike": 155.0,
                "expiry": "2026-04-30",
            },
        ],
        "broker_status": "PendingSubmit",
    }


def test_trade_cycle_command_records_risk_rejection_in_dashboard(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    scanner = FakeScanner([_spread()])
    state_path = tmp_path / "dashboard.json"
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=scanner,
            decision_service=StubDecisionService(),
            account_service=FakeAccountService(_account()),
            position_service=FakePositionService(open_symbol_count=0),
            risk_guard=StubRiskGuard(allowed=False, reason="max_open_risk_pct"),
            executor=StubExecutor(),
            dashboard_state_path=state_path,
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade-cycle"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "trade-cycle",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "live_enabled": False,
        "mode": "paper",
        "reason": "max_open_risk_pct",
        "status": "risk_rejected",
    }
    assert read_dashboard_snapshot(state_path) == {
        "status": "updated",
        "last_cycle": {
            "status": "risk_rejected",
            "reason": "max_open_risk_pct",
        },
        "error": None,
    }


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
    assert executor.open_calls == []
    assert executor.close_calls == []


def test_run_trade_cycle_rejects_unknown_decision_action() -> None:
    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=StubDecisionService(action="ship-it"),
        executor=StubExecutor(),
    )

    assert result["status"] == "decision_error"
    assert result["reason"] == "invalid_action"
    assert result["action"] == "ship-it"


def test_run_trade_cycle_rejects_when_approved_spread_does_not_match_candidates() -> None:
    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=StubDecisionService(short_strike=165),
        executor=StubExecutor(),
    )

    assert result == {
        "status": "decision_rejected",
        "reason": "approved_candidate_not_found",
    }


def test_run_trade_cycle_returns_structured_error_when_decision_service_raises() -> None:
    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=StubDecisionService(error=RuntimeError("service offline")),
        executor=StubExecutor(),
    )

    assert result == {
        "status": "decision_error",
        "reason": "decision_service_failed",
        "error_type": "RuntimeError",
        "message": "service offline",
    }


def test_run_trade_cycle_returns_structured_error_for_missing_approval_expiry() -> None:
    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=PayloadDecisionService(
            {
                "action": "approve",
                "ticker": "AMD",
                "strategy": "bull_put_credit_spread",
                "short_strike": 160,
                "long_strike": 155,
                "limit_credit": 1.05,
            }
        ),
        executor=StubExecutor(),
    )

    assert result == {
        "status": "decision_error",
        "reason": "invalid_approval",
        "error_type": "AttributeError",
        "message": "'Decision' object has no attribute 'expiry'",
    }


def test_run_trade_cycle_returns_structured_error_for_missing_approval_strikes() -> None:
    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=PayloadDecisionService(
            {
                "action": "approve",
                "ticker": "AMD",
                "strategy": "bull_put_credit_spread",
                "expiry": "2026-04-30",
                "limit_credit": 1.05,
            }
        ),
        executor=StubExecutor(),
    )

    assert result == {
        "status": "decision_error",
        "reason": "invalid_approval",
        "error_type": "AttributeError",
        "message": "'Decision' object has no attribute 'short_strike'",
    }


def test_run_trade_cycle_returns_structured_error_for_none_limit_credit() -> None:
    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=StubDecisionService(limit_credit=None),
        executor=StubExecutor(),
    )

    assert result == {
        "status": "decision_error",
        "reason": "invalid_approval",
        "error_type": "TypeError",
        "message": "float() argument must be a string or a real number, not 'NoneType'",
    }


def test_run_trade_cycle_returns_structured_error_for_zero_limit_credit() -> None:
    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=StubDecisionService(limit_credit=0),
        executor=StubExecutor(),
    )

    assert result == {
        "status": "decision_error",
        "reason": "invalid_approval",
        "error_type": "ValueError",
        "message": "limit_credit must be a positive finite number",
    }


def test_run_trade_cycle_returns_structured_error_for_nan_limit_credit() -> None:
    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=StubDecisionService(limit_credit=math.nan),
        executor=StubExecutor(),
    )

    assert result == {
        "status": "decision_error",
        "reason": "invalid_approval",
        "error_type": "ValueError",
        "message": "limit_credit must be a positive finite number",
    }


def test_run_trade_cycle_stops_when_risk_guard_rejects_candidate() -> None:
    executor = StubExecutor()
    risk_guard = StubRiskGuard(allowed=False, reason="max_open_risk_pct")
    approved = _spread(short_strike=170, long_strike=165, max_loss=700)

    result = run_trade_cycle(
        candidates=[_spread(), approved],
        account=_account(),
        decision_service=StubDecisionService(short_strike=170, long_strike=165),
        executor=executor,
        risk_guard=risk_guard,
    )

    assert result == {"status": "risk_rejected", "reason": "max_open_risk_pct"}
    assert len(risk_guard.calls) == 1
    assert risk_guard.calls[0][0] is approved
    assert executor.open_calls == []
    assert executor.close_calls == []


def test_run_trade_cycle_fails_closed_when_risk_guard_is_missing() -> None:
    executor = StubExecutor()

    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=StubDecisionService(),
        executor=executor,
        risk_guard=None,
    )

    assert result == {
        "status": "risk_rejected",
        "reason": "risk_guard_missing",
    }
    assert executor.open_calls == []
    assert executor.close_calls == []


def test_run_trade_cycle_submits_order_when_candidate_and_risk_pass() -> None:
    first_spread = _spread()
    approved = _spread(short_strike=170, long_strike=165, credit=1.2)
    executor = StubExecutor()
    risk_guard = StubRiskGuard(allowed=True)

    result = run_trade_cycle(
        candidates=[first_spread, approved],
        account=_account(),
        decision_service=StubDecisionService(short_strike=170, long_strike=165),
        executor=executor,
        risk_guard=risk_guard,
    )

    assert result["status"] == "submitted"
    assert result["payload"]["symbol"] == "AMD"
    assert result["payload"]["short_strike"] == 170
    assert result["payload"]["long_strike"] == 165
    assert risk_guard.calls[0][0] is approved
    assert executor.open_calls[0][0] is approved
    assert executor.open_calls[0][1] == 1.05
    assert executor.open_calls[0][2] == 1
    assert executor.close_calls == []


def test_run_trade_cycle_uses_sell_to_open_combo_with_ibkr_executor() -> None:
    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=StubDecisionService(limit_credit=1.15),
        executor=IbkrExecutor(
            client=_FakeIbClient(),
            ibkr_module=_FakeIbModule(),
        ),
        risk_guard=StubRiskGuard(allowed=True),
    )

    assert result["status"] == "submitted"
    assert result["broker"] == "ibkr"
    assert result["contract"] == {
        "symbol": "AMD",
        "secType": "BAG",
        "currency": "USD",
        "exchange": "SMART",
    }
    assert result["legs"] == [
        {
            "con_id": 50160,
            "action": "SELL",
            "ratio": 1,
            "exchange": "SMART",
            "right": "P",
            "strike": 160.0,
            "expiry": "2026-04-30",
        },
        {
            "con_id": 50155,
            "action": "BUY",
            "ratio": 1,
            "exchange": "SMART",
            "right": "P",
            "strike": 155.0,
            "expiry": "2026-04-30",
        },
    ]
    assert result["order"] == {
        "action": "SELL",
        "orderType": "LMT",
        "totalQuantity": 1,
        "lmtPrice": 1.15,
        "transmit": True,
    }
    assert result["broker_status"] == "PendingSubmit"


def test_run_trade_cycle_returns_structured_error_when_executor_raises() -> None:
    result = run_trade_cycle(
        candidates=[_spread()],
        account=_account(),
        decision_service=StubDecisionService(),
        executor=StubExecutor(error=RuntimeError("ibkr down")),
        risk_guard=StubRiskGuard(allowed=True),
    )

    assert result == {
        "status": "executor_error",
        "reason": "submission_failed",
        "error_type": "RuntimeError",
        "message": "ibkr down",
    }


def test_build_dashboard_snapshot_returns_default_shape() -> None:
    assert build_dashboard_snapshot() == {
        "status": "idle",
        "last_cycle": {},
        "error": None,
    }


def test_read_dashboard_snapshot_returns_default_shape_for_missing_state(
    tmp_path: Path,
) -> None:
    snapshot = read_dashboard_snapshot(tmp_path / "dashboard.json")

    assert snapshot == {
        "status": "idle",
        "last_cycle": {},
        "error": None,
    }


def test_read_dashboard_snapshot_returns_error_shape_for_corrupt_state(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "dashboard.json"
    state_path.write_text("{invalid", encoding="utf-8")

    snapshot = read_dashboard_snapshot(state_path)

    assert snapshot["status"] == "error"
    assert snapshot["last_cycle"] == {}
    assert snapshot["error"] == {
        "type": "StateStoreError",
        "message": f"invalid state file: {state_path}",
    }


def test_read_dashboard_snapshot_normalizes_nested_fields(tmp_path: Path) -> None:
    state_path = tmp_path / "dashboard.json"
    state_path.write_text(
        json.dumps(
            {
                "status": 123,
                "last_cycle": {
                    "status": ["bad"],
                    "payload": "not-a-dict",
                    "reason": 99,
                },
                "error": {
                    "type": 7,
                    "message": ["oops"],
                },
            }
        ),
        encoding="utf-8",
    )

    snapshot = read_dashboard_snapshot(state_path)

    assert snapshot == {
        "status": "idle",
        "last_cycle": {"payload": {}},
        "error": None,
    }


def test_update_dashboard_state_round_trips_snapshot(tmp_path: Path) -> None:
    state_path = tmp_path / "dashboard.json"
    last_cycle = {"status": "submitted", "payload": {"symbol": "AMD"}}

    written = update_dashboard_state(state_path, last_cycle=last_cycle)
    loaded = read_dashboard_snapshot(state_path)

    assert written == loaded == {
        "status": "updated",
        "last_cycle": last_cycle,
        "error": None,
    }


def test_update_dashboard_state_preserves_manage_anomaly_fields(tmp_path: Path) -> None:
    state_path = tmp_path / "dashboard.json"
    last_cycle = {
        "status": "anomaly",
        "reason": "uncertain_submit_state",
        "ticker": "AMD",
        "fingerprints": ["AMD|2026-04-30|P|160.0|155.0|1"],
        "manual_intervention_required": True,
    }

    written = update_dashboard_state(state_path, last_cycle=last_cycle)
    loaded = read_dashboard_snapshot(state_path)

    assert written == loaded == {
        "status": "updated",
        "last_cycle": last_cycle,
        "error": None,
    }
