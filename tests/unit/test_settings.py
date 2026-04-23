from pathlib import Path

import pytest
from pydantic import ValidationError

from trader_shawn.settings import load_settings


def write_config(
    config_dir: Path,
    *,
    app: str | None = None,
    symbols: str | None = None,
    risk: str | None = None,
    providers: str | None = None,
    events: str | None = None,
) -> None:
    (config_dir / "app.yaml").write_text(
        app
        or "mode: paper\nlive_enabled: false\nibkr:\n  host: 127.0.0.1\n  port: 4002\n  client_id: 7\naudit_db_path: runtime/audit.db\n",
        encoding="utf-8",
    )
    (config_dir / "symbols.yaml").write_text(
        symbols or "symbols:\n  - SPY\n  - QQQ\n  - GOOG\n  - AMD\n  - NVDA\n",
        encoding="utf-8",
    )
    (config_dir / "risk.yaml").write_text(
        risk
        or "max_risk_per_trade_pct: 0.02\nmax_daily_loss_pct: 0.04\nmax_new_positions_per_day: 6\nmax_open_risk_pct: 0.20\nmax_spreads_per_symbol: 2\nprofit_take_pct: 0.50\nstop_loss_multiple: 2.0\nexit_dte_threshold: 5\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        providers
        or "provider_mode: claude_primary\nprimary_provider: claude_cli\nsecondary_provider: codex\nprovider_timeout_seconds: 15\nsecondary_timeout_seconds: 10\n",
        encoding="utf-8",
    )
    (config_dir / "events.yaml").write_text(
        events or "events: []\n",
        encoding="utf-8",
    )


def test_load_settings_merges_yaml_and_env(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir)

    monkeypatch.setenv("TRADER_SHAWN_MODE", "live")
    monkeypatch.setenv("TRADER_SHAWN_LIVE_ENABLED", "true")
    monkeypatch.setenv("TRADER_SHAWN_MARKET_DATA_TYPE", "live")
    monkeypatch.setenv("TRADER_SHAWN_IBKR_HOST", "10.0.0.8")
    monkeypatch.setenv("TRADER_SHAWN_IBKR_PORT", "4002")
    monkeypatch.setenv("TRADER_SHAWN_IBKR_CLIENT_ID", "19")

    settings = load_settings(config_dir)

    assert settings.mode == "live"
    assert settings.live_enabled is True
    assert settings.market_data_type == "live"
    assert settings.ibkr.host == "10.0.0.8"
    assert settings.ibkr.port == 4002
    assert settings.ibkr.client_id == 19
    assert settings.symbols == ["SPY", "QQQ", "GOOG", "AMD", "NVDA"]
    assert settings.risk.max_risk_per_trade_pct == 0.02
    assert settings.providers.primary_provider == "claude_cli"
    assert settings.audit_db_path == tmp_path / "runtime" / "audit.db"


def test_load_settings_uses_ib_gateway_paper_default_port(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir)

    settings = load_settings(config_dir)

    assert settings.mode == "paper"
    assert settings.live_enabled is False
    assert settings.ibkr.host == "127.0.0.1"
    assert settings.ibkr.port == 4002
    assert settings.ibkr.client_id == 7
    assert settings.market_data_type == "live"


def test_load_settings_accepts_delayed_market_data_for_paper_mode(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(
        config_dir,
        app="mode: paper\nlive_enabled: false\nmarket_data_type: delayed\nibkr:\n  host: 127.0.0.1\n  port: 4002\n  client_id: 7\naudit_db_path: runtime/audit.db\n",
    )

    settings = load_settings(config_dir)

    assert settings.market_data_type == "delayed"


def test_load_settings_rejects_delayed_market_data_for_live_mode(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(
        config_dir,
        app="mode: live\nlive_enabled: true\nmarket_data_type: delayed\nibkr:\n  host: 127.0.0.1\n  port: 4002\n  client_id: 7\naudit_db_path: runtime/audit.db\n",
    )

    with pytest.raises(ValidationError, match="live_mode_requires_live_market_data"):
        load_settings(config_dir)


def test_load_settings_rejects_invalid_boolean_env(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir)

    monkeypatch.setenv("TRADER_SHAWN_LIVE_ENABLED", "sometimes")

    with pytest.raises(ValueError, match="TRADER_SHAWN_LIVE_ENABLED"):
        load_settings(config_dir)


def test_load_settings_rejects_invalid_ibkr_int_env(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir)

    monkeypatch.setenv("TRADER_SHAWN_IBKR_PORT", "seven")

    with pytest.raises(ValueError, match="TRADER_SHAWN_IBKR_PORT"):
        load_settings(config_dir)


def test_load_settings_rejects_malformed_symbols_shape(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir, symbols="symbols: SPY\n")

    with pytest.raises(ValidationError):
        load_settings(config_dir)


def test_load_settings_rejects_malformed_events_shape(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir, events="events:\n  name: fed\n")

    with pytest.raises(ValidationError):
        load_settings(config_dir)


@pytest.mark.parametrize(
    ("risk", "expected_field"),
    [
        (
            "max_risk_per_trade_pct: -0.01\nmax_daily_loss_pct: 0.04\nmax_new_positions_per_day: 6\nmax_open_risk_pct: 0.20\nmax_spreads_per_symbol: 2\nprofit_take_pct: 0.50\nstop_loss_multiple: 2.0\nexit_dte_threshold: 5\n",
            "max_risk_per_trade_pct",
        ),
        (
            "max_risk_per_trade_pct: 0.02\nmax_daily_loss_pct: 0.04\nmax_new_positions_per_day: 6\nmax_open_risk_pct: 1.20\nmax_spreads_per_symbol: 2\nprofit_take_pct: 0.50\nstop_loss_multiple: 2.0\nexit_dte_threshold: 5\n",
            "max_open_risk_pct",
        ),
        (
            "max_risk_per_trade_pct: 0.02\nmax_daily_loss_pct: 0.04\nmax_new_positions_per_day: -1\nmax_open_risk_pct: 0.20\nmax_spreads_per_symbol: 2\nprofit_take_pct: 0.50\nstop_loss_multiple: 2.0\nexit_dte_threshold: 5\n",
            "max_new_positions_per_day",
        ),
        (
            "max_risk_per_trade_pct: 0.02\nmax_daily_loss_pct: 0.04\nmax_new_positions_per_day: 6\nmax_open_risk_pct: 0.20\nmax_spreads_per_symbol: 0\nprofit_take_pct: 0.50\nstop_loss_multiple: 2.0\nexit_dte_threshold: 5\n",
            "max_spreads_per_symbol",
        ),
    ],
)
def test_load_settings_rejects_invalid_risk_values(
    tmp_path: Path, risk: str, expected_field: str
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir, risk=risk)

    with pytest.raises(ValidationError, match=expected_field):
        load_settings(config_dir)


@pytest.mark.parametrize(
    ("providers", "expected_field"),
    [
        (
            "provider_mode: claude_primary\nprimary_provider: claude_cli\nsecondary_provider: codex\nprovider_timeout_seconds: 0\nsecondary_timeout_seconds: 10\n",
            "provider_timeout_seconds",
        ),
        (
            "provider_mode: claude_primary\nprimary_provider: claude_cli\nsecondary_provider: codex\nprovider_timeout_seconds: 15\nsecondary_timeout_seconds: -5\n",
            "secondary_timeout_seconds",
        ),
    ],
)
def test_load_settings_rejects_invalid_provider_values(
    tmp_path: Path, providers: str, expected_field: str
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir, providers=providers)

    with pytest.raises(ValidationError, match=expected_field):
        load_settings(config_dir)


def test_load_settings_rejects_malformed_app_yaml_with_validation_error(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir, app="- mode: paper\n")

    with pytest.raises(ValidationError):
        load_settings(config_dir)


def test_load_settings_rejects_invalid_mode_from_app_yaml(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(
        config_dir,
        app="mode: dry_run\nlive_enabled: false\nibkr:\n  host: 127.0.0.1\n  port: 4002\n  client_id: 7\naudit_db_path: runtime/audit.db\n",
    )

    with pytest.raises(ValidationError, match="mode"):
        load_settings(config_dir)


def test_load_settings_rejects_invalid_mode_env(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir)

    monkeypatch.setenv("TRADER_SHAWN_MODE", "dry_run")

    with pytest.raises(ValidationError, match="mode"):
        load_settings(config_dir)


def test_load_settings_rejects_live_mode_when_live_disabled(
    monkeypatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir)

    monkeypatch.setenv("TRADER_SHAWN_MODE", "live")
    monkeypatch.setenv("TRADER_SHAWN_LIVE_ENABLED", "false")

    with pytest.raises(ValidationError) as exc_info:
        load_settings(config_dir)

    errors = exc_info.value.errors(include_url=False)

    assert len(errors) == 1
    assert errors[0]["loc"] == ()
    assert errors[0]["type"] == "live_mode_requires_live_enabled"
    assert errors[0]["msg"] == "live_enabled must be true when mode=live"
    assert errors[0]["ctx"] == {"mode": "live"}
