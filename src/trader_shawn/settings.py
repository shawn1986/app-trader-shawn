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


class SymbolsSettings(BaseModel):
    symbols: list[str]


class EventsSettings(BaseModel):
    events: list[dict[str, Any]]


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


def _parse_bool_env(name: str, fallback: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return fallback

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a valid boolean string")


def _parse_int_env(name: str, fallback: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return fallback

    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid integer") from exc


def load_settings(config_dir: Path) -> AppSettings:
    config_dir = config_dir.resolve()
    app_data = _load_yaml(config_dir / "app.yaml")
    symbols_data = _load_yaml(config_dir / "symbols.yaml")
    risk_data = _load_yaml(config_dir / "risk.yaml")
    providers_data = _load_yaml(config_dir / "providers.yaml")
    events_data = _load_yaml(config_dir / "events.yaml")

    app_data["mode"] = os.getenv("TRADER_SHAWN_MODE", app_data["mode"])
    app_data["live_enabled"] = _parse_bool_env(
        "TRADER_SHAWN_LIVE_ENABLED", app_data["live_enabled"]
    )

    ibkr_data = dict(app_data["ibkr"])
    ibkr_data["host"] = os.getenv("TRADER_SHAWN_IBKR_HOST", ibkr_data["host"])
    ibkr_data["port"] = _parse_int_env("TRADER_SHAWN_IBKR_PORT", ibkr_data["port"])
    ibkr_data["client_id"] = _parse_int_env(
        "TRADER_SHAWN_IBKR_CLIENT_ID", ibkr_data["client_id"]
    )
    app_data["ibkr"] = ibkr_data

    audit_db_path = Path(app_data["audit_db_path"])
    if not audit_db_path.is_absolute():
        audit_db_path = (config_dir.parent / audit_db_path).resolve()

    merged_data = {
        **app_data,
        "audit_db_path": audit_db_path,
        "symbols": SymbolsSettings.model_validate(symbols_data).symbols,
        "risk": risk_data,
        "providers": providers_data,
        "events": EventsSettings.model_validate(events_data).events,
    }
    return AppSettings.model_validate(merged_data)
