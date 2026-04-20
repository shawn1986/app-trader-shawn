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
        or "mode: paper\nlive_enabled: false\nibkr:\n  host: 127.0.0.1\n  port: 7497\n  client_id: 7\naudit_db_path: runtime/audit.db\n",
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
    monkeypatch.setenv("TRADER_SHAWN_IBKR_HOST", "10.0.0.8")
    monkeypatch.setenv("TRADER_SHAWN_IBKR_PORT", "4002")
    monkeypatch.setenv("TRADER_SHAWN_IBKR_CLIENT_ID", "19")

    settings = load_settings(config_dir)

    assert settings.mode == "live"
    assert settings.live_enabled is True
    assert settings.ibkr.host == "10.0.0.8"
    assert settings.ibkr.port == 4002
    assert settings.ibkr.client_id == 19
    assert settings.symbols == ["SPY", "QQQ", "GOOG", "AMD", "NVDA"]
    assert settings.risk.max_risk_per_trade_pct == 0.02
    assert settings.providers.primary_provider == "claude_cli"
    assert settings.audit_db_path == tmp_path / "runtime" / "audit.db"


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
