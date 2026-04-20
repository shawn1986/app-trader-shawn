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
