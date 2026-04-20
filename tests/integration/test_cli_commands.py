from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from trader_shawn.app import cli


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


def test_scan_command_returns_configuration_contract() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["scan"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "candidate_builder": {
            "max_abs_delta": 0.25,
            "max_bid_ask_ratio": 0.15,
            "max_width": 5,
            "min_abs_delta": 0.15,
            "min_open_interest": 100,
            "min_volume": 50,
            "strategy": "bull_put_credit_spread",
        },
        "command": "scan",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "limitations": [
            "CLI scan reports configured symbol universe and candidate filters only; live market data retrieval is not composed here."
        ],
        "live_enabled": False,
        "mode": "paper",
        "status": "ok",
        "symbols": ["SPY", "QQQ", "GOOG", "AMD", "NVDA"],
    }


def test_decide_command_returns_provider_contract() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["decide"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "decide",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "decision": {
            "provider_mode": "claude_primary",
            "primary_provider": "claude_cli",
            "secondary_provider": "codex",
            "secondary_timeout_seconds": 10,
            "timeout_seconds": 15,
        },
        "limitations": [
            "CLI decide reports configured AI provider routing only; candidate generation and live provider execution are not composed here."
        ],
        "live_enabled": False,
        "mode": "paper",
        "status": "ok",
    }


def test_trade_command_returns_execution_contract() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["trade"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "trade",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "execution": {
            "broker": "ibkr",
            "entry_path": "open_credit_spread_combo",
            "host": "127.0.0.1",
            "port": 7497,
        },
        "limitations": [
            "CLI trade reports execution and risk settings only; it does not build candidates, request approvals, or submit a broker order by itself."
        ],
        "live_enabled": False,
        "mode": "paper",
        "risk": {
            "guard_required": True,
            "max_daily_loss_pct": 0.04,
            "max_open_risk_pct": 0.2,
            "max_risk_per_trade_pct": 0.02,
            "max_spreads_per_symbol": 2,
        },
        "status": "ok",
    }


def test_manage_command_returns_exit_contract() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["manage"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "manage",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "exit_rules": {
            "exit_dte_threshold": 5,
            "profit_take_pct": 0.5,
            "stop_loss_multiple": 2.0,
            "supported_order_path": "close_credit_spread_combo",
        },
        "limitations": [
            "CLI manage reports configured exit rules only; fetching live positions and submitting close orders are not composed here."
        ],
        "live_enabled": False,
        "mode": "paper",
        "status": "ok",
    }


def test_trade_cycle_command_returns_workflow_contract() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["trade-cycle"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "command": "trade-cycle",
        "config_dir": str((Path.cwd() / "config").resolve()),
        "limitations": [
            "CLI trade-cycle reports the configured orchestration contract only; scan, AI decisions, account snapshots, and broker side effects still require explicit composition."
        ],
        "live_enabled": False,
        "mode": "paper",
        "status": "ok",
        "workflow": {
            "entry_order_path": "open_credit_spread_combo",
            "fail_closed_without_risk_guard": True,
            "manage_command_available": True,
            "scan_command_available": True,
        },
    }
