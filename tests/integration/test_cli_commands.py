from __future__ import annotations

from click.testing import CliRunner

from trader_shawn.app import cli


def test_cli_has_expected_subcommands() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "scan" in result.output
    assert "decide" in result.output
    assert "trade" in result.output
    assert "manage" in result.output
