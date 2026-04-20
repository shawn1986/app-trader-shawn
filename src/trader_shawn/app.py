from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Sequence

import click

from trader_shawn.candidate_builder.credit_spread_builder import (
    MAX_ABS_DELTA,
    MAX_BID_ASK_RATIO,
    MAX_WIDTH,
    MIN_ABS_DELTA,
    MIN_OPEN_INTEREST,
    MIN_VOLUME,
)
from trader_shawn.domain.enums import DecisionAction
from trader_shawn.domain.models import AccountSnapshot, CandidateSpread
from trader_shawn.settings import AppSettings, load_settings


def run_trade_cycle(
    *,
    candidates: Sequence[CandidateSpread],
    account: AccountSnapshot,
    decision_service: Any,
    executor: Any,
    risk_guard: Any | None = None,
    open_symbol_count: int = 0,
) -> dict[str, Any]:
    if not candidates:
        return {"status": "no_candidates"}

    selected = candidates[0]
    context = {
        "ticker": selected.ticker,
        "candidate": selected,
        "candidates": list(candidates),
        "account": account,
    }
    try:
        decision = decision_service.decide(context)
    except Exception as exc:
        return _error_result(
            status="decision_error",
            reason="decision_service_failed",
            exc=exc,
        )

    try:
        action = DecisionAction(getattr(decision, "action"))
    except (AttributeError, TypeError, ValueError):
        return {
            "status": "decision_error",
            "reason": "invalid_action",
            "action": getattr(decision, "action", None),
        }
    if action is not DecisionAction.APPROVE:
        return {
            "status": "decision_rejected",
            "reason": getattr(decision, "reason", ""),
            "action": action.value,
        }

    try:
        approved_candidate_key = _approved_candidate_key(decision)
        limit_credit = _decision_limit_credit(decision)
    except (AttributeError, TypeError, ValueError) as exc:
        return _error_result(
            status="decision_error",
            reason="invalid_approval",
            exc=exc,
        )

    matched_spread = _resolve_approved_candidate(candidates, approved_candidate_key)
    if matched_spread is None:
        return {
            "status": "decision_rejected",
            "reason": "approved_candidate_not_found",
        }

    if risk_guard is None:
        return {"status": "risk_rejected", "reason": "risk_guard_missing"}

    guard_result = risk_guard.evaluate(
        matched_spread,
        account,
        open_symbol_count,
    )
    if not guard_result.allowed:
        return {"status": "risk_rejected", "reason": guard_result.reason}

    try:
        return executor.submit_open_credit_spread(
            matched_spread,
            limit_credit=limit_credit,
            quantity=1,
        )
    except Exception as exc:
        return _error_result(
            status="executor_error",
            reason="submission_failed",
            exc=exc,
        )


@click.group(name="trader-shawn")
def cli() -> None:
    """Trader Shawn command line interface."""


@cli.command("trade-cycle")
def trade_cycle_command() -> None:
    click.echo(json.dumps(_trade_cycle_command(), sort_keys=True))


@cli.command("scan")
def scan_command() -> None:
    click.echo(json.dumps(_scan_command(), sort_keys=True))


@cli.command("decide")
def decide_command() -> None:
    click.echo(json.dumps(_decide_command(), sort_keys=True))


@cli.command("trade")
def trade_command() -> None:
    click.echo(json.dumps(_trade_command(), sort_keys=True))


@cli.command("manage")
def manage_command() -> None:
    click.echo(json.dumps(_manage_command(), sort_keys=True))


@cli.command("dashboard")
@click.argument("state_path", type=click.Path(path_type=Path))
def dashboard_command(state_path: Path) -> None:
    click.echo(json.dumps(_dashboard_command(state_path), sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    cli.main(
        args=list(argv) if argv is not None else None,
        prog_name="trader-shawn",
        standalone_mode=False,
    )
    return 0


def _approved_candidate_key(decision: Any) -> tuple[str, str, float, float]:
    return (
        str(getattr(decision, "ticker")),
        str(getattr(decision, "expiry")),
        float(getattr(decision, "short_strike")),
        float(getattr(decision, "long_strike")),
    )


def _resolve_approved_candidate(
    candidates: Sequence[CandidateSpread],
    approved_candidate_key: tuple[str, str, float, float],
) -> CandidateSpread | None:
    ticker, expiry, short_strike, long_strike = approved_candidate_key

    for candidate in candidates:
        if (
            candidate.ticker == ticker
            and candidate.expiry == expiry
            and _strikes_match(candidate.short_strike, short_strike)
            and _strikes_match(candidate.long_strike, long_strike)
        ):
            return candidate
    return None


def _decision_limit_credit(decision: Any) -> float:
    limit_credit = float(getattr(decision, "limit_credit"))
    if not math.isfinite(limit_credit) or limit_credit <= 0:
        raise ValueError("limit_credit must be a positive finite number")
    return limit_credit


def _strikes_match(candidate_strike: float | None, approved_strike: float) -> bool:
    return (
        candidate_strike is not None
        and math.isclose(float(candidate_strike), approved_strike, rel_tol=0.0, abs_tol=1e-9)
    )


def _error_result(*, status: str, reason: str, exc: Exception) -> dict[str, str]:
    return {
        "status": status,
        "reason": reason,
        "error_type": type(exc).__name__,
        "message": str(exc),
    }


def _trade_cycle_command() -> dict[str, Any]:
    settings, config_dir = _load_command_settings()
    if settings is None:
        return config_dir
    return {
        **_command_envelope("trade-cycle", settings=settings, config_dir=config_dir),
        "workflow": {
            "entry_order_path": "open_credit_spread_combo",
            "fail_closed_without_risk_guard": True,
            "manage_command_available": True,
            "scan_command_available": True,
        },
        "limitations": [
            "CLI trade-cycle reports the configured orchestration contract only; scan, AI decisions, account snapshots, and broker side effects still require explicit composition."
        ],
    }


def _scan_command() -> dict[str, Any]:
    settings, config_dir = _load_command_settings()
    if settings is None:
        return config_dir
    return {
        **_command_envelope("scan", settings=settings, config_dir=config_dir),
        "symbols": settings.symbols,
        "candidate_builder": {
            "strategy": "bull_put_credit_spread",
            "min_open_interest": MIN_OPEN_INTEREST,
            "min_volume": MIN_VOLUME,
            "min_abs_delta": MIN_ABS_DELTA,
            "max_abs_delta": MAX_ABS_DELTA,
            "max_width": MAX_WIDTH,
            "max_bid_ask_ratio": MAX_BID_ASK_RATIO,
        },
        "limitations": [
            "CLI scan reports configured symbol universe and candidate filters only; live market data retrieval is not composed here."
        ],
    }


def _decide_command() -> dict[str, Any]:
    settings, config_dir = _load_command_settings()
    if settings is None:
        return config_dir
    return {
        **_command_envelope("decide", settings=settings, config_dir=config_dir),
        "decision": {
            "provider_mode": settings.providers.provider_mode,
            "primary_provider": settings.providers.primary_provider,
            "secondary_provider": settings.providers.secondary_provider,
            "timeout_seconds": settings.providers.provider_timeout_seconds,
            "secondary_timeout_seconds": settings.providers.secondary_timeout_seconds,
        },
        "limitations": [
            "CLI decide reports configured AI provider routing only; candidate generation and live provider execution are not composed here."
        ],
    }


def _trade_command() -> dict[str, Any]:
    settings, config_dir = _load_command_settings()
    if settings is None:
        return config_dir
    return {
        **_command_envelope("trade", settings=settings, config_dir=config_dir),
        "execution": {
            "broker": "ibkr",
            "entry_path": "open_credit_spread_combo",
            "host": settings.ibkr.host,
            "port": settings.ibkr.port,
        },
        "risk": {
            "guard_required": True,
            "max_risk_per_trade_pct": settings.risk.max_risk_per_trade_pct,
            "max_daily_loss_pct": settings.risk.max_daily_loss_pct,
            "max_open_risk_pct": settings.risk.max_open_risk_pct,
            "max_spreads_per_symbol": settings.risk.max_spreads_per_symbol,
        },
        "limitations": [
            "CLI trade reports execution and risk settings only; it does not build candidates, request approvals, or submit a broker order by itself."
        ],
    }


def _manage_command() -> dict[str, Any]:
    settings, config_dir = _load_command_settings()
    if settings is None:
        return config_dir
    return {
        **_command_envelope("manage", settings=settings, config_dir=config_dir),
        "exit_rules": {
            "profit_take_pct": settings.risk.profit_take_pct,
            "stop_loss_multiple": settings.risk.stop_loss_multiple,
            "exit_dte_threshold": settings.risk.exit_dte_threshold,
            "supported_order_path": "close_credit_spread_combo",
        },
        "limitations": [
            "CLI manage reports configured exit rules only; fetching live positions and submitting close orders are not composed here."
        ],
    }


def _dashboard_command(state_path: str | Path) -> dict[str, Any]:
    from trader_shawn.monitoring.dashboard_api import read_dashboard_snapshot

    return read_dashboard_snapshot(state_path)


def _load_command_settings() -> tuple[AppSettings | None, Path | dict[str, Any]]:
    config_dir = (Path.cwd() / "config").resolve()
    try:
        return load_settings(config_dir), config_dir
    except Exception as exc:
        return None, {
            "status": "error",
            "reason": "config_load_failed",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "config_dir": str(config_dir),
        }


def _command_envelope(
    command: str,
    *,
    settings: AppSettings,
    config_dir: Path,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "command": command,
        "mode": settings.mode,
        "live_enabled": settings.live_enabled,
        "config_dir": str(config_dir),
    }


if __name__ == "__main__":
    raise SystemExit(main())
