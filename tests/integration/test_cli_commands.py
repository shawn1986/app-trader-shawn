from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

import trader_shawn.app as app_module
from trader_shawn.app import cli
from trader_shawn.domain.models import AccountSnapshot, CandidateSpread


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
        ibkr=SimpleNamespace(
            host="127.0.0.1",
            port=7497,
        ),
    )


class FakeScanner:
    def __init__(self, candidates: list[CandidateSpread]) -> None:
        self.candidates = list(candidates)
        self.calls: list[list[str]] = []

    def scan_candidates(self, symbols: list[str]) -> list[CandidateSpread]:
        self.calls.append(list(symbols))
        return list(self.candidates)


class FakeDecisionService:
    def __init__(self, *, decision: object) -> None:
        self.decision = decision
        self.calls: list[dict[str, object]] = []

    def decide(self, context: dict[str, object]) -> object:
        self.calls.append(context)
        return self.decision


class FakeAccountService:
    def __init__(self, snapshot: AccountSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def fetch_account_snapshot(self) -> AccountSnapshot:
        self.calls += 1
        return self.snapshot


class ExplodingAccountService:
    def fetch_account_snapshot(self) -> AccountSnapshot:
        raise AssertionError("account snapshot should not be fetched")


class FakePositionService:
    def __init__(self, *, open_symbol_count: int = 0) -> None:
        self.open_symbol_count = open_symbol_count
        self.count_calls: list[str] = []

    def count_open_option_positions(self, ticker: str) -> int:
        self.count_calls.append(ticker)
        return self.open_symbol_count


class FakeRiskGuard:
    def __init__(self, *, allowed: bool = True, reason: str = "ok") -> None:
        self.allowed = allowed
        self.reason = reason
        self.calls: list[tuple[CandidateSpread, AccountSnapshot, int]] = []

    def evaluate(
        self,
        spread: CandidateSpread,
        account: AccountSnapshot,
        open_symbol_count: int,
    ) -> object:
        self.calls.append((spread, account, open_symbol_count))
        return SimpleNamespace(allowed=self.allowed, reason=self.reason)


class FakeExecutor:
    def __init__(self) -> None:
        self.open_calls: list[tuple[CandidateSpread, float, int]] = []

    def submit_open_credit_spread(
        self,
        spread: CandidateSpread,
        *,
        limit_credit: float,
        quantity: int = 1,
    ) -> dict[str, object]:
        self.open_calls.append((spread, limit_credit, quantity))
        return {
            "status": "submitted",
            "payload": {
                "symbol": spread.ticker,
                "expiry": spread.expiry,
                "short_strike": spread.short_strike,
                "long_strike": spread.long_strike,
                "limit_credit": limit_credit,
                "quantity": quantity,
            },
        }


def _runtime(
    *,
    scanner: FakeScanner | None = None,
    decision_service: FakeDecisionService | None = None,
    account_service: FakeAccountService | None = None,
    position_service: FakePositionService | None = None,
    position_manager: object | None = None,
    risk_guard: FakeRiskGuard | None = None,
    executor: FakeExecutor | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        settings=_settings(),
        config_dir=(Path.cwd() / "config").resolve(),
        scanner=scanner,
        decision_service=decision_service,
        account_service=account_service,
        position_service=position_service,
        position_manager=position_manager,
        risk_guard=risk_guard,
        executor=executor,
        dashboard_state_path=None,
    )


def test_cli_has_expected_subcommands() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "trade-cycle" in result.output
    assert "dashboard" in result.output
    assert "scan" in result.output
    assert "decide" in result.output
    assert "trade" in result.output
    assert "manage" in result.output


def test_dashboard_command_returns_default_snapshot_for_missing_file(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["dashboard", str(tmp_path / "missing.json")])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "status": "idle",
        "last_cycle": {},
        "error": None,
    }


def test_dashboard_command_returns_structured_error_for_directory_path(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["dashboard", str(tmp_path)])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "status": "error",
        "last_cycle": {},
        "error": {
            "type": "OSError",
            "message": str(tmp_path),
        },
    }


def test_scan_command_executes_runtime_scan_and_returns_candidates(monkeypatch) -> None:
    runner = CliRunner()
    scanner = FakeScanner([_spread(), _spread(ticker="NVDA", short_strike=110, long_strike=105)])
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(scanner=scanner),
        raising=False,
    )

    result = runner.invoke(cli, ["scan"])

    assert result.exit_code == 0
    payload = json.loads(result.output)

    assert payload["status"] == "ok"
    assert payload["command"] == "scan"
    assert payload["candidate_count"] == 2
    assert payload["candidates"][0]["ticker"] == "AMD"
    assert payload["candidates"][1]["ticker"] == "NVDA"
    assert scanner.calls == [["SPY", "QQQ", "AMD"]]


def test_decide_command_executes_scan_then_decision(monkeypatch) -> None:
    runner = CliRunner()
    candidate = _spread()
    scanner = FakeScanner([candidate])
    decision_service = FakeDecisionService(
        decision=SimpleNamespace(
            action="approve",
            ticker="AMD",
            strategy="bull_put_credit_spread",
            expiry="2026-04-30",
            short_strike=160,
            long_strike=155,
            limit_credit=1.05,
            reason="looks good",
        )
    )
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(scanner=scanner, decision_service=decision_service),
        raising=False,
    )

    result = runner.invoke(cli, ["decide"])

    assert result.exit_code == 0
    payload = json.loads(result.output)

    assert payload["status"] == "ok"
    assert payload["command"] == "decide"
    assert payload["candidate_count"] == 1
    assert payload["decision"]["action"] == "approve"
    assert payload["decision"]["limit_credit"] == 1.05
    assert scanner.calls == [["SPY", "QQQ", "AMD"]]
    assert decision_service.calls[0]["ticker"] == candidate.ticker
    assert decision_service.calls[0]["candidates"] == [candidate]
    assert decision_service.calls[0]["candidate"] == candidate


def test_trade_command_executes_entry_workflow(monkeypatch) -> None:
    runner = CliRunner()
    candidate = _spread()
    scanner = FakeScanner([candidate])
    account_service = FakeAccountService(_account())
    position_service = FakePositionService(open_symbol_count=1)
    risk_guard = FakeRiskGuard(allowed=True)
    executor = FakeExecutor()
    decision_service = FakeDecisionService(
        decision=SimpleNamespace(
            action="approve",
            ticker="AMD",
            strategy="bull_put_credit_spread",
            expiry="2026-04-30",
            short_strike=160,
            long_strike=155,
            limit_credit=1.10,
            reason="approved",
        )
    )
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
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade"])

    assert result.exit_code == 0
    payload = json.loads(result.output)

    assert payload["status"] == "submitted"
    assert payload["command"] == "trade"
    assert payload["payload"]["symbol"] == "AMD"
    assert payload["payload"]["limit_credit"] == 1.1
    assert scanner.calls == [["SPY", "QQQ", "AMD"]]
    assert account_service.calls == 1
    assert position_service.count_calls == ["AMD"]
    assert risk_guard.calls[0][0] is candidate
    assert executor.open_calls == [(candidate, 1.1, 1)]


def test_trade_command_returns_no_candidates_without_fetching_account(monkeypatch) -> None:
    runner = CliRunner()
    scanner = FakeScanner([])
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=scanner,
            account_service=ExplodingAccountService(),
            position_service=FakePositionService(open_symbol_count=0),
            risk_guard=FakeRiskGuard(allowed=True),
            executor=FakeExecutor(),
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "trade",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "live_enabled": False,
        "mode": "paper",
        "status": "no_candidates",
    }


def test_build_cli_runtime_wires_position_manager(tmp_path: Path, monkeypatch) -> None:
    config_dir = (Path.cwd() / "config").resolve()
    settings = SimpleNamespace(
        mode="paper",
        live_enabled=False,
        symbols=["AMD"],
        events=[{"ticker": "AMD", "date": "2026-04-30"}],
        risk=SimpleNamespace(
            max_risk_per_trade_pct=0.02,
            max_daily_loss_pct=0.04,
            max_new_positions_per_day=2,
            max_open_risk_pct=0.2,
            max_spreads_per_symbol=2,
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        providers=SimpleNamespace(
            primary_provider="claude_cli",
            secondary_provider="codex",
            provider_timeout_seconds=15,
            secondary_timeout_seconds=10,
        ),
        ibkr=SimpleNamespace(
            host="127.0.0.1",
            port=7497,
            client_id=12,
        ),
        audit_db_path=tmp_path / "audit.db",
    )
    market_data = object()
    audit_logger = object()
    executor = object()
    earnings_calendar = object()
    decision_service = object()
    risk_guard = object()
    position_manager = object()
    captured: dict[str, object] = {}

    def fake_market_data_client(**kwargs: object) -> object:
        captured["market_data_kwargs"] = kwargs
        return market_data

    def fake_audit_logger(db_path: Path) -> object:
        captured["audit_db_path"] = db_path
        return audit_logger

    def fake_earnings_calendar(events: object) -> object:
        captured["events"] = events
        return earnings_calendar

    def fake_executor(**kwargs: object) -> object:
        captured["executor_kwargs"] = kwargs
        return executor

    def fake_risk_guard(risk_settings: object) -> object:
        captured["risk_settings"] = risk_settings
        return risk_guard

    def fake_position_manager(**kwargs: object) -> object:
        captured["position_manager_kwargs"] = kwargs
        return position_manager

    monkeypatch.setattr(app_module, "load_settings", lambda _config_dir: settings)
    monkeypatch.setattr(
        app_module,
        "IbkrMarketDataClient",
        fake_market_data_client,
    )
    monkeypatch.setattr(
        app_module,
        "AuditLogger",
        fake_audit_logger,
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "EarningsCalendar",
        fake_earnings_calendar,
    )
    monkeypatch.setattr(
        app_module,
        "IbkrExecutor",
        fake_executor,
    )
    monkeypatch.setattr(app_module, "_build_decision_service", lambda _settings: decision_service)
    monkeypatch.setattr(
        app_module,
        "RiskGuard",
        fake_risk_guard,
    )
    monkeypatch.setattr(
        app_module,
        "PositionManager",
        fake_position_manager,
        raising=False,
    )

    runtime = app_module.build_cli_runtime()

    assert runtime.settings is settings
    assert runtime.config_dir == config_dir
    assert runtime.account_service is market_data
    assert runtime.position_service is market_data
    assert runtime.decision_service is decision_service
    assert runtime.executor is executor
    assert runtime.risk_guard is risk_guard
    assert runtime.position_manager is position_manager
    assert captured["audit_db_path"] == settings.audit_db_path
    assert captured["events"] == settings.events
    assert captured["position_manager_kwargs"] == {
        "audit_logger": audit_logger,
        "market_data": market_data,
        "executor": executor,
        "earnings_calendar": earnings_calendar,
        "risk_settings": settings.risk,
        "mode": settings.mode,
    }


def test_manage_command_fails_closed_without_supported_runtime(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(),
        raising=False,
    )

    result = runner.invoke(cli, ["manage"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "manage",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "live_enabled": False,
        "mode": "paper",
        "reason": "position_management_not_supported",
        "status": "runtime_unavailable",
    }


def test_manage_command_executes_real_runtime_manager(monkeypatch) -> None:
    runner = CliRunner()
    manager = SimpleNamespace(
        manage_positions=lambda: {
            "status": "submitted",
            "position_id": "pos-1",
            "ticker": "AMD",
            "exit_reason": "take_profit",
            "payload": {"order_id": 404},
        }
    )
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(position_manager=manager),
        raising=False,
    )

    result = runner.invoke(cli, ["manage"])

    assert result.exit_code == 0
    payload = json.loads(result.output)

    assert payload["status"] == "submitted"
    assert payload["command"] == "manage"
    assert payload["position_id"] == "pos-1"
    assert payload["ticker"] == "AMD"
    assert payload["exit_reason"] == "take_profit"
    assert payload["payload"] == {"order_id": 404}


def test_manage_command_updates_dashboard_snapshot(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    state_path = tmp_path / "dashboard.json"
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: SimpleNamespace(
            settings=_settings(),
            config_dir=(Path.cwd() / "config").resolve(),
            position_manager=SimpleNamespace(
                manage_positions=lambda: {"status": "ok", "managed_count": 1}
            ),
            dashboard_state_path=state_path,
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["manage"])

    assert result.exit_code == 0
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "status": "updated",
        "last_cycle": {"status": "ok", "managed_count": 1},
        "error": None,
    }
