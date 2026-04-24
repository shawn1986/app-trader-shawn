from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, Field, TypeAdapter, model_validator
from pydantic_core import PydanticCustomError


Percent = Annotated[float, Field(gt=0, le=1)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveFloat = Annotated[float, Field(gt=0)]
YamlMapping = TypeAdapter(dict[str, Any])


class IBKRSettings(BaseModel):
    host: str
    port: int
    client_id: int
    request_timeout_seconds: PositiveInt = 30


class RiskSettings(BaseModel):
    max_risk_per_trade_pct: Percent
    max_daily_loss_pct: Percent
    max_new_positions_per_day: PositiveInt
    max_open_risk_pct: Percent
    max_spreads_per_symbol: PositiveInt
    profit_take_pct: Percent
    stop_loss_multiple: PositiveFloat
    exit_dte_threshold: NonNegativeInt


class ProviderSettings(BaseModel):
    provider_mode: str
    primary_provider: str
    secondary_provider: str
    provider_timeout_seconds: PositiveInt
    secondary_timeout_seconds: PositiveInt


class SymbolsSettings(BaseModel):
    symbols: list[str]


class EventsSettings(BaseModel):
    events: list[dict[str, Any]]


class AppConfigSettings(BaseModel):
    mode: Literal["paper", "live"]
    live_enabled: bool
    market_data_type: Literal["live", "delayed"] = "live"
    ibkr: IBKRSettings
    audit_db_path: Path

    @model_validator(mode="after")
    def validate_live_mode(self) -> "AppConfigSettings":
        if self.mode == "live" and not self.live_enabled:
            raise PydanticCustomError(
                "live_mode_requires_live_enabled",
                "live_enabled must be true when mode=live",
                {"mode": self.mode},
            )
        if self.mode == "live" and self.market_data_type != "live":
            raise PydanticCustomError(
                "live_mode_requires_live_market_data",
                "market_data_type must be live when mode=live",
                {"mode": self.mode, "market_data_type": self.market_data_type},
            )
        return self


class AppSettings(AppConfigSettings):
    symbols: list[str]
    risk: RiskSettings
    providers: ProviderSettings
    events: list[dict[str, Any]]


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    return YamlMapping.validate_python(_load_yaml(path))


def _parse_bool_env(name: str, fallback: Any) -> Any:
    raw_value = os.getenv(name)
    if raw_value is None:
        return fallback

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a valid boolean string")


def _parse_int_env(name: str, fallback: Any) -> Any:
    raw_value = os.getenv(name)
    if raw_value is None:
        return fallback

    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid integer") from exc


def load_settings(config_dir: Path) -> AppSettings:
    config_dir = config_dir.resolve()
    app_data = _load_yaml_mapping(config_dir / "app.yaml")

    app_data["mode"] = os.getenv("TRADER_SHAWN_MODE", app_data.get("mode"))
    app_data["live_enabled"] = _parse_bool_env(
        "TRADER_SHAWN_LIVE_ENABLED", app_data.get("live_enabled")
    )
    market_data_type_env = os.getenv("TRADER_SHAWN_MARKET_DATA_TYPE")
    if market_data_type_env is not None:
        app_data["market_data_type"] = market_data_type_env

    raw_ibkr_data = app_data.get("ibkr")
    ibkr_data: Any = (
        dict(raw_ibkr_data)
        if isinstance(raw_ibkr_data, dict)
        else ({} if raw_ibkr_data is None else raw_ibkr_data)
    )
    if isinstance(ibkr_data, dict):
        ibkr_data["host"] = os.getenv("TRADER_SHAWN_IBKR_HOST", ibkr_data.get("host"))
        ibkr_data["port"] = _parse_int_env("TRADER_SHAWN_IBKR_PORT", ibkr_data.get("port"))
        ibkr_data["client_id"] = _parse_int_env(
            "TRADER_SHAWN_IBKR_CLIENT_ID", ibkr_data.get("client_id")
        )
        request_timeout_seconds = _parse_int_env(
            "TRADER_SHAWN_IBKR_REQUEST_TIMEOUT_SECONDS",
            ibkr_data.get("request_timeout_seconds"),
        )
        if request_timeout_seconds is not None:
            ibkr_data["request_timeout_seconds"] = request_timeout_seconds
    app_data["ibkr"] = ibkr_data

    app_settings = AppConfigSettings.model_validate(app_data)
    audit_db_path = app_settings.audit_db_path
    if not audit_db_path.is_absolute():
        audit_db_path = (config_dir.parent / audit_db_path).resolve()

    symbols_data = _load_yaml_mapping(config_dir / "symbols.yaml")
    risk_data = _load_yaml_mapping(config_dir / "risk.yaml")
    providers_data = _load_yaml_mapping(config_dir / "providers.yaml")
    events_data = _load_yaml_mapping(config_dir / "events.yaml")

    merged_data = {
        **app_settings.model_dump(),
        "audit_db_path": audit_db_path,
        "symbols": SymbolsSettings.model_validate(symbols_data).symbols,
        "risk": risk_data,
        "providers": providers_data,
        "events": EventsSettings.model_validate(events_data).events,
    }
    return AppSettings.model_validate(merged_data)
