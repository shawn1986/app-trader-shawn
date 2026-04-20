from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class IBKRSettings(BaseModel):
    host: str
    port: int
    client_id: int


class RiskSettings(BaseModel):
    max_risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_new_positions_per_day: int
    max_open_risk_pct: float
    max_spreads_per_symbol: int
    profit_take_pct: float
    stop_loss_multiple: float
    exit_dte_threshold: int


class ProviderSettings(BaseModel):
    provider_mode: str
    primary_provider: str
    secondary_provider: str
    provider_timeout_seconds: int
    secondary_timeout_seconds: int


class AppSettings(BaseModel):
    mode: str
    live_enabled: bool
    ibkr: IBKRSettings
    audit_db_path: Path
    symbols: list[str]
    risk: RiskSettings
    providers: ProviderSettings
    events: list[dict[str, Any]]


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise TypeError(f"Expected mapping in {path}")
    return data


def load_settings(config_dir: Path) -> AppSettings:
    app_data = _load_yaml(config_dir / "app.yaml")
    symbols_data = _load_yaml(config_dir / "symbols.yaml")
    risk_data = _load_yaml(config_dir / "risk.yaml")
    providers_data = _load_yaml(config_dir / "providers.yaml")
    events_data = _load_yaml(config_dir / "events.yaml")

    app_data["mode"] = os.getenv("TRADER_SHAWN_MODE", app_data["mode"])
    app_data["live_enabled"] = os.getenv(
        "TRADER_SHAWN_LIVE_ENABLED", str(app_data["live_enabled"])
    ).lower() in {"1", "true", "yes", "on"}

    ibkr_data = dict(app_data["ibkr"])
    ibkr_data["host"] = os.getenv("TRADER_SHAWN_IBKR_HOST", ibkr_data["host"])
    ibkr_data["port"] = int(os.getenv("TRADER_SHAWN_IBKR_PORT", str(ibkr_data["port"])))
    ibkr_data["client_id"] = int(
        os.getenv("TRADER_SHAWN_IBKR_CLIENT_ID", str(ibkr_data["client_id"]))
    )
    app_data["ibkr"] = ibkr_data

    return AppSettings(
        mode=app_data["mode"],
        live_enabled=app_data["live_enabled"],
        ibkr=IBKRSettings.model_validate(app_data["ibkr"]),
        audit_db_path=Path(app_data["audit_db_path"]),
        symbols=list(symbols_data["symbols"]),
        risk=RiskSettings.model_validate(risk_data),
        providers=ProviderSettings.model_validate(providers_data),
        events=list(events_data.get("events", [])),
    )
