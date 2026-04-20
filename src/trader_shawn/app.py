from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Sequence

import click

from trader_shawn.domain.enums import DecisionAction, PositionSide
from trader_shawn.domain.models import AccountSnapshot, CandidateSpread, PositionSnapshot


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

    if risk_guard is not None:
        guard_result = risk_guard.evaluate(
            matched_spread,
            account,
            open_symbol_count,
        )
        if not guard_result.allowed:
            return {"status": "risk_rejected", "reason": guard_result.reason}

    position = PositionSnapshot(
        ticker=matched_spread.ticker,
        quantity=1,
        side=PositionSide.SHORT,
        strategy=matched_spread.strategy,
        expiry=matched_spread.expiry,
        short_strike=matched_spread.short_strike,
        long_strike=matched_spread.long_strike,
        entry_credit=matched_spread.credit,
    )
    try:
        return executor.submit_limit_combo(
            position,
            limit_price=limit_credit,
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


def _trade_cycle_command() -> dict[str, str]:
    return {"status": "not_implemented"}


def _dashboard_command(state_path: str | Path) -> dict[str, Any]:
    from trader_shawn.monitoring.dashboard_api import read_dashboard_snapshot

    return read_dashboard_snapshot(state_path)


if __name__ == "__main__":
    raise SystemExit(main())
