from __future__ import annotations

import argparse
import json
import math
from typing import Any, Sequence

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

    matched_spread = _resolve_approved_candidate(candidates, decision)
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

    try:
        limit_credit = _decision_limit_credit(decision)
    except (AttributeError, TypeError, ValueError) as exc:
        return _error_result(
            status="decision_error",
            reason="invalid_approval",
            exc=exc,
        )

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


def cli(argv: Sequence[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = args.handler(args)
    if result is not None:
        print(json.dumps(result, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return cli(argv)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trader-shawn")
    subparsers = parser.add_subparsers(dest="command", required=True)

    trade_cycle = subparsers.add_parser("trade-cycle", help="Run one trade cycle")
    trade_cycle.set_defaults(handler=_trade_cycle_command)

    dashboard = subparsers.add_parser("dashboard", help="Print dashboard status")
    dashboard.add_argument("state_path", help="Path to the persisted dashboard state JSON file")
    dashboard.set_defaults(handler=_dashboard_command)
    return parser


def _resolve_approved_candidate(
    candidates: Sequence[CandidateSpread],
    decision: Any,
) -> CandidateSpread | None:
    try:
        ticker = str(getattr(decision, "ticker"))
        expiry = str(getattr(decision, "expiry"))
        short_strike = float(getattr(decision, "short_strike"))
        long_strike = float(getattr(decision, "long_strike"))
    except (AttributeError, TypeError, ValueError):
        return None

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


def _trade_cycle_command(_: argparse.Namespace) -> dict[str, str]:
    return {"status": "not_implemented"}


def _dashboard_command(args: argparse.Namespace) -> dict[str, Any]:
    from trader_shawn.monitoring.dashboard_api import read_dashboard_snapshot

    return read_dashboard_snapshot(args.state_path)


if __name__ == "__main__":
    raise SystemExit(main())
