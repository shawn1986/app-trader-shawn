from pathlib import Path

from trader_shawn.settings import load_settings


def test_load_settings_merges_yaml_and_env(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    (config_dir / "app.yaml").write_text(
        "mode: paper\nlive_enabled: false\nibkr:\n  host: 127.0.0.1\n  port: 7497\n  client_id: 7\naudit_db_path: runtime/audit.db\n",
        encoding="utf-8",
    )
    (config_dir / "symbols.yaml").write_text(
        "symbols:\n  - SPY\n  - QQQ\n  - GOOG\n  - AMD\n  - NVDA\n",
        encoding="utf-8",
    )
    (config_dir / "risk.yaml").write_text(
        "max_risk_per_trade_pct: 0.02\nmax_daily_loss_pct: 0.04\nmax_new_positions_per_day: 6\nmax_open_risk_pct: 0.20\nmax_spreads_per_symbol: 2\nprofit_take_pct: 0.50\nstop_loss_multiple: 2.0\nexit_dte_threshold: 5\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "provider_mode: claude_primary\nprimary_provider: claude_cli\nsecondary_provider: codex\nprovider_timeout_seconds: 15\nsecondary_timeout_seconds: 10\n",
        encoding="utf-8",
    )
    (config_dir / "events.yaml").write_text("events: []\n", encoding="utf-8")

    monkeypatch.setenv("TRADER_SHAWN_MODE", "live")
    monkeypatch.setenv("TRADER_SHAWN_LIVE_ENABLED", "true")

    settings = load_settings(config_dir)

    assert settings.mode == "live"
    assert settings.live_enabled is True
    assert settings.symbols == ["SPY", "QQQ", "GOOG", "AMD", "NVDA"]
    assert settings.risk.max_risk_per_trade_pct == 0.02
    assert settings.providers.primary_provider == "claude_cli"
