from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

import trader_shawn.app as app_module
from trader_shawn.app import cli
from trader_shawn.domain.models import AccountSnapshot, CandidateSpread
from trader_shawn.monitoring.audit_logger import AuditLogger


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
            max_new_positions_per_day=2,
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
            port=4002,
            client_id=12,
        ),
        events=[],
        audit_db_path=(Path.cwd() / "runtime" / "audit.db").resolve(),
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
    def __init__(
        self,
        *,
        open_symbol_count: int = 0,
        open_symbol_count_by_ticker: dict[str, int] | None = None,
    ) -> None:
        self.open_symbol_count = open_symbol_count
        self.open_symbol_count_by_ticker = dict(open_symbol_count_by_ticker or {})
        self.count_calls: list[str] = []

    def count_open_option_positions(self, ticker: str) -> int:
        self.count_calls.append(ticker)
        return self.open_symbol_count_by_ticker.get(ticker, self.open_symbol_count)


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


class UncertainOpenExecutor(FakeExecutor):
    def submit_open_credit_spread(
        self,
        spread: CandidateSpread,
        *,
        limit_credit: float,
        quantity: int = 1,
    ) -> dict[str, object]:
        self.open_calls.append((spread, limit_credit, quantity))
        raise RuntimeError("place order failed")


def _runtime(
    *,
    scanner: FakeScanner | None = None,
    decision_service: FakeDecisionService | None = None,
    account_service: FakeAccountService | None = None,
    position_service: FakePositionService | None = None,
    position_manager: object | None = None,
    risk_guard: FakeRiskGuard | None = None,
    executor: FakeExecutor | None = None,
    audit_logger: AuditLogger | None = None,
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
        audit_logger=audit_logger,
        dashboard_state_path=None,
    )


def _install_real_builder_stubs(
    monkeypatch,
    tmp_path: Path,
    *,
    settings: SimpleNamespace | None = None,
    position_manager: object | None = None,
) -> dict[str, object]:
    config_dir = (tmp_path / "config").resolve()
    runtime_root = config_dir.parent
    effective_settings = settings or _settings()
    if getattr(effective_settings, "audit_db_path", None) is None:
        effective_settings.audit_db_path = (runtime_root / "audit.db").resolve()

    market_data = object()
    audit_logger = object()
    earnings_calendar = object()
    executor = object()
    decision_service = object()
    risk_guard = object()
    captured: dict[str, object] = {}

    def fake_load_settings(received_config_dir: Path) -> SimpleNamespace:
        captured["config_dir_arg"] = received_config_dir
        return effective_settings

    def fake_market_data_client(**kwargs: object) -> object:
        captured["market_data_kwargs"] = kwargs
        return market_data

    def fake_earnings_calendar(events: object) -> object:
        captured["events"] = events
        return earnings_calendar

    def fake_executor(**kwargs: object) -> object:
        captured["executor_kwargs"] = kwargs
        return executor

    def fake_audit_logger(db_path: Path) -> object:
        captured["audit_db_path"] = db_path
        return audit_logger

    def fake_risk_guard(risk_settings: object) -> object:
        captured["risk_settings"] = risk_settings
        return risk_guard

    def fake_position_manager(**kwargs: object) -> object:
        captured["position_manager_kwargs"] = kwargs
        return position_manager if position_manager is not None else object()

    monkeypatch.setattr(app_module, "_default_config_dir", lambda: config_dir)
    monkeypatch.setattr(app_module, "load_settings", fake_load_settings)
    monkeypatch.setattr(app_module, "IbkrMarketDataClient", fake_market_data_client)
    monkeypatch.setattr(app_module, "EarningsCalendar", fake_earnings_calendar)
    monkeypatch.setattr(app_module, "IbkrExecutor", fake_executor)
    monkeypatch.setattr(app_module, "_build_decision_service", lambda _settings: decision_service)
    monkeypatch.setattr(app_module, "AuditLogger", fake_audit_logger, raising=False)
    monkeypatch.setattr(app_module, "RiskGuard", fake_risk_guard)
    monkeypatch.setattr(app_module, "PositionManager", fake_position_manager, raising=False)

    return {
        "config_dir": config_dir,
        "settings": effective_settings,
        "market_data": market_data,
        "audit_logger": audit_logger,
        "earnings_calendar": earnings_calendar,
        "executor": executor,
        "decision_service": decision_service,
        "risk_guard": risk_guard,
        "captured": captured,
    }


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


def test_trade_command_counts_positions_for_approved_candidate_ticker(monkeypatch) -> None:
    runner = CliRunner()
    first_candidate = _spread(ticker="AMD", short_strike=160, long_strike=155)
    approved_candidate = _spread(ticker="NVDA", short_strike=110, long_strike=105)
    scanner = FakeScanner([first_candidate, approved_candidate])
    account_service = FakeAccountService(_account())
    position_service = FakePositionService(
        open_symbol_count_by_ticker={"AMD": 0, "NVDA": 2}
    )
    risk_guard = FakeRiskGuard(allowed=True)
    executor = FakeExecutor()
    decision_service = FakeDecisionService(
        decision=SimpleNamespace(
            action="approve",
            ticker="NVDA",
            strategy="bull_put_credit_spread",
            expiry="2026-04-30",
            short_strike=110,
            long_strike=105,
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
    assert payload["payload"]["symbol"] == "NVDA"
    assert position_service.count_calls == ["NVDA"]
    assert risk_guard.calls[0][0] is approved_candidate
    assert risk_guard.calls[0][2] == 2
    assert executor.open_calls == [(approved_candidate, 1.1, 1)]


def test_trade_command_persists_submitted_open_position(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    candidate = _spread()
    audit_logger = AuditLogger(tmp_path / "audit.db")
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
            audit_logger=audit_logger,
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade"])

    assert result.exit_code == 0
    positions = audit_logger.fetch_active_managed_positions(mode="paper")

    assert len(positions) == 1
    assert positions[0]["ticker"] == "AMD"
    assert positions[0]["status"] == "opening"
    assert positions[0]["entry_credit"] == 1.1
    assert positions[0]["broker_fingerprint"] == "AMD|2026-04-30|P|160.0|155.0|1"


def test_trade_command_reports_audit_error_without_masking_submitted_order(monkeypatch) -> None:
    runner = CliRunner()
    candidate = _spread()

    class ExplodingAuditLogger:
        def upsert_managed_position(self, record: object) -> None:
            raise RuntimeError("sqlite busy")

    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=FakeScanner([candidate]),
            decision_service=FakeDecisionService(
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
            ),
            account_service=FakeAccountService(_account()),
            position_service=FakePositionService(open_symbol_count=1),
            risk_guard=FakeRiskGuard(allowed=True),
            executor=FakeExecutor(),
            audit_logger=ExplodingAuditLogger(),
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "audit_error": {
            "type": "RuntimeError",
            "message": "sqlite busy",
        },
        "command": "trade",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "live_enabled": False,
        "mode": "paper",
        "payload": {
            "expiry": "2026-04-30",
            "limit_credit": 1.1,
            "long_strike": 155,
            "quantity": 1,
            "short_strike": 160,
            "symbol": "AMD",
        },
        "status": "submitted",
    }


def test_trade_command_persists_uncertain_open_and_returns_anomaly(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    candidate = _spread()
    audit_logger = AuditLogger(tmp_path / "audit.db")
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=FakeScanner([candidate]),
            decision_service=FakeDecisionService(
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
            ),
            account_service=FakeAccountService(_account()),
            position_service=FakePositionService(open_symbol_count=1),
            risk_guard=FakeRiskGuard(allowed=True),
            executor=UncertainOpenExecutor(),
            audit_logger=audit_logger,
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "trade",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "fingerprints": ["AMD|2026-04-30|P|160.0|155.0|1"],
        "live_enabled": False,
        "manual_intervention_required": True,
        "mode": "paper",
        "reason": "uncertain_submit_state",
        "status": "anomaly",
    }
    positions = audit_logger.fetch_active_managed_positions(mode="paper")
    assert len(positions) == 1
    assert positions[0]["status"] == "opening"
    assert positions[0]["broker_fingerprint"] == "AMD|2026-04-30|P|160.0|155.0|1"
    assert audit_logger.fetch_position_events(positions[0]["position_id"])[-1]["event_type"] == (
        "open_submit_uncertain"
    )


def test_trade_command_preserves_uncertain_open_anomaly_when_audit_write_fails(
    monkeypatch,
) -> None:
    runner = CliRunner()
    candidate = _spread()

    class ExplodingAuditLogger:
        def upsert_managed_position(self, record: object) -> None:
            raise RuntimeError("sqlite busy")

    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=FakeScanner([candidate]),
            decision_service=FakeDecisionService(
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
            ),
            account_service=FakeAccountService(_account()),
            position_service=FakePositionService(open_symbol_count=0),
            risk_guard=FakeRiskGuard(allowed=True),
            executor=UncertainOpenExecutor(),
            audit_logger=ExplodingAuditLogger(),
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "audit_error": {
            "message": "sqlite busy",
            "type": "RuntimeError",
        },
        "command": "trade",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "fingerprints": ["AMD|2026-04-30|P|160.0|155.0|1"],
        "live_enabled": False,
        "manual_intervention_required": True,
        "mode": "paper",
        "reason": "uncertain_submit_state",
        "status": "anomaly",
    }


def test_trade_command_blocks_when_uncertain_open_is_unresolved(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    candidate = _spread()
    audit_logger = AuditLogger(tmp_path / "audit.db")
    audit_logger.upsert_managed_position(
        {
            "position_id": "trade-open-uncertain",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.10,
            "entry_order_id": None,
            "mode": "paper",
            "status": "opening",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by trade",
            "risk_note": "",
        }
    )
    audit_logger.record_position_event(
        "trade-open-uncertain",
        "open_submit_uncertain",
        {
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "error": "place order failed",
        },
    )
    executor = FakeExecutor()
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=FakeScanner([candidate]),
            decision_service=FakeDecisionService(
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
            ),
            account_service=FakeAccountService(_account()),
            position_service=FakePositionService(open_symbol_count=0),
            risk_guard=FakeRiskGuard(allowed=True),
            executor=executor,
            audit_logger=audit_logger,
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "trade",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "fingerprints": ["AMD|2026-04-30|P|160.0|155.0|1"],
        "live_enabled": False,
        "manual_intervention_required": True,
        "mode": "paper",
        "reason": "uncertain_submit_state",
        "status": "anomaly",
    }
    assert executor.open_calls == []


def test_trade_command_fail_closes_when_audit_lookup_raises(monkeypatch) -> None:
    runner = CliRunner()
    candidate = _spread()

    class ExplodingAuditReader:
        def fetch_active_managed_positions(self, *, mode: str) -> list[object]:
            raise RuntimeError("audit unreadable")

        def fetch_position_events(self, position_id: str) -> list[object]:
            raise AssertionError("position events should not be fetched after audit read failure")

    executor = FakeExecutor()
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=FakeScanner([candidate]),
            decision_service=FakeDecisionService(
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
            ),
            account_service=FakeAccountService(_account()),
            position_service=FakePositionService(open_symbol_count=0),
            risk_guard=FakeRiskGuard(allowed=True),
            executor=executor,
            audit_logger=ExplodingAuditReader(),
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "audit_error": {
            "message": "audit unreadable",
            "type": "RuntimeError",
        },
        "command": "trade",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "live_enabled": False,
        "manual_intervention_required": True,
        "mode": "paper",
        "reason": "audit_lookup_failed",
        "status": "anomaly",
    }
    assert executor.open_calls == []


def test_trade_command_blocks_when_open_submission_is_pending(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    candidate = _spread()
    audit_logger = AuditLogger(tmp_path / "audit.db")
    audit_logger.upsert_managed_position(
        {
            "position_id": "trade-open-pending",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.10,
            "entry_order_id": 123,
            "mode": "paper",
            "status": "opening",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by trade",
            "risk_note": "",
        }
    )
    audit_logger.record_position_event(
        "trade-open-pending",
        "open_submitted",
        {
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "entry_order_id": 123,
        },
    )
    executor = FakeExecutor()
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=FakeScanner([candidate]),
            decision_service=FakeDecisionService(
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
            ),
            account_service=FakeAccountService(_account()),
            position_service=FakePositionService(open_symbol_count=0),
            risk_guard=FakeRiskGuard(allowed=True),
            executor=executor,
            audit_logger=audit_logger,
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "trade",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "fingerprints": ["AMD|2026-04-30|P|160.0|155.0|1"],
        "live_enabled": False,
        "manual_intervention_required": True,
        "mode": "paper",
        "reason": "pending_open_submission",
        "status": "anomaly",
    }
    assert executor.open_calls == []


def test_trade_command_blocks_when_opening_position_exists_without_event(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    candidate = _spread()
    audit_logger = AuditLogger(tmp_path / "audit.db")
    audit_logger.upsert_managed_position(
        {
            "position_id": "trade-open-pending-no-event",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.10,
            "entry_order_id": 123,
            "mode": "paper",
            "status": "opening",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by trade",
            "risk_note": "",
        }
    )
    executor = FakeExecutor()
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            scanner=FakeScanner([candidate]),
            decision_service=FakeDecisionService(
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
            ),
            account_service=FakeAccountService(_account()),
            position_service=FakePositionService(open_symbol_count=0),
            risk_guard=FakeRiskGuard(allowed=True),
            executor=executor,
            audit_logger=audit_logger,
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["trade"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "trade",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "fingerprints": ["AMD|2026-04-30|P|160.0|155.0|1"],
        "live_enabled": False,
        "manual_intervention_required": True,
        "mode": "paper",
        "reason": "pending_open_submission",
        "status": "anomaly",
    }
    assert executor.open_calls == []


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
    settings = _settings(symbols=["AMD"])
    settings.events = [{"ticker": "AMD", "date": "2026-04-30"}]
    settings.audit_db_path = (tmp_path / "audit.db").resolve()
    position_manager = object()
    builder = _install_real_builder_stubs(
        monkeypatch,
        tmp_path,
        settings=settings,
        position_manager=position_manager,
    )
    captured = builder["captured"]

    runtime = app_module.build_cli_runtime()

    assert runtime.settings is settings
    assert runtime.config_dir == builder["config_dir"]
    assert runtime.account_service is builder["market_data"]
    assert runtime.position_service is builder["market_data"]
    assert runtime.decision_service is builder["decision_service"]
    assert runtime.executor is builder["executor"]
    assert runtime.risk_guard is builder["risk_guard"]
    assert runtime.position_manager is position_manager
    assert captured["audit_db_path"] == settings.audit_db_path
    assert captured["events"] == settings.events
    assert captured["market_data_kwargs"]["client_id"] == settings.ibkr.client_id
    assert captured["executor_kwargs"]["client_id"] == settings.ibkr.client_id + 1
    assert captured["position_manager_kwargs"] == {
        "audit_logger": builder["audit_logger"],
        "market_data": builder["market_data"],
        "executor": builder["executor"],
        "earnings_calendar": builder["earnings_calendar"],
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


def test_manage_command_executes_real_runtime_manager(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    expected_result = {
        "status": "submitted",
        "position_id": "pos-1",
        "ticker": "AMD",
        "exit_reason": "take_profit",
        "payload": {"order_id": 404},
    }
    manager = SimpleNamespace(
        manage_positions=lambda: {
            **expected_result,
        }
    )
    builder = _install_real_builder_stubs(
        monkeypatch,
        tmp_path,
        position_manager=manager,
    )

    result = runner.invoke(cli, ["manage"])

    assert result.exit_code == 0
    payload = json.loads(result.output)

    assert payload["status"] == expected_result["status"]
    assert payload["command"] == "manage"
    assert payload["position_id"] == expected_result["position_id"]
    assert payload["ticker"] == expected_result["ticker"]
    assert payload["exit_reason"] == expected_result["exit_reason"]
    assert payload["payload"] == expected_result["payload"]
    assert payload["config_dir"] == str(builder["config_dir"])


def test_manage_command_surfaces_uncertain_submit_state(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    audit_logger = AuditLogger(tmp_path / "audit.db")
    audit_logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "closing",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": 0.42,
            "last_evaluated_at": "2026-04-20T10:00:00+00:00",
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "close in progress",
            "risk_note": "",
        }
    )
    audit_logger.record_position_event(
        "pos-1",
        "close_submit_uncertain",
        {
            "exit_reason": "take_profit",
            "limit_price": 0.42,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "error": "submit temporarily unavailable",
        },
    )

    def raise_uncertain() -> dict[str, object]:
        raise RuntimeError("submit temporarily unavailable")

    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: _runtime(
            position_manager=SimpleNamespace(manage_positions=raise_uncertain),
            audit_logger=audit_logger,
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["manage"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "manage",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "fingerprints": ["AMD|2026-04-30|P|160.0|155.0|1"],
        "live_enabled": False,
        "manual_intervention_required": True,
        "mode": "paper",
        "reason": "uncertain_submit_state",
        "status": "anomaly",
    }


def test_manage_command_updates_dashboard_snapshot_via_real_builder(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    manager = SimpleNamespace(
        manage_positions=lambda: {"status": "ok", "managed_count": 1}
    )
    builder = _install_real_builder_stubs(
        monkeypatch,
        tmp_path,
        position_manager=manager,
    )
    state_path = (builder["config_dir"].parent / "runtime" / "dashboard.json").resolve()

    result = runner.invoke(cli, ["manage"])

    assert result.exit_code == 0
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "status": "updated",
        "last_cycle": {"status": "ok", "managed_count": 1},
        "error": None,
    }


def test_scan_command_real_builder_ignores_unwritable_audit_db_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    blocked_parent = tmp_path / "blocked-parent"
    blocked_parent.write_text("not-a-directory", encoding="utf-8")
    settings = _settings()
    settings.audit_db_path = blocked_parent / "audit.db"

    monkeypatch.setattr(
        app_module,
        "_default_config_dir",
        lambda: (tmp_path / "config").resolve(),
    )
    monkeypatch.setattr(app_module, "load_settings", lambda _config_dir: settings)
    monkeypatch.setattr(app_module, "IbkrMarketDataClient", lambda **kwargs: object())
    monkeypatch.setattr(app_module, "EarningsCalendar", lambda events: object())
    monkeypatch.setattr(
        app_module,
        "CliScanner",
        lambda **kwargs: SimpleNamespace(scan_candidates=lambda symbols: []),
    )
    monkeypatch.setattr(app_module, "IbkrExecutor", lambda **kwargs: object())
    monkeypatch.setattr(app_module, "_build_decision_service", lambda _settings: object())
    monkeypatch.setattr(app_module, "RiskGuard", lambda risk_settings: object())

    result = runner.invoke(cli, ["scan"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "scan",
        "config_dir": str((tmp_path / "config").resolve()),
        "live_enabled": False,
        "mode": "paper",
        "status": "ok",
        "candidate_count": 0,
        "candidates": [],
    }
    assert not settings.audit_db_path.exists()
