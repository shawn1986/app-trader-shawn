from __future__ import annotations

import argparse
import json
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
    decision = decision_service.decide(context)
    action = DecisionAction(getattr(decision, "action"))
    if action is not DecisionAction.APPROVE:
        return {
            "status": "decision_rejected",
            "reason": getattr(decision, "reason", ""),
            "action": action.value,
        }

    if risk_guard is not None:
        guard_result = risk_guard.evaluate(selected, account, open_symbol_count)
        if not guard_result.allowed:
            return {"status": "risk_rejected", "reason": guard_result.reason}

    position = PositionSnapshot(
        ticker=getattr(decision, "ticker", selected.ticker) or selected.ticker,
        quantity=1,
        side=PositionSide.SHORT,
        strategy=getattr(decision, "strategy", selected.strategy) or selected.strategy,
        expiry=getattr(decision, "expiry", selected.expiry) or selected.expiry,
        short_strike=getattr(decision, "short_strike", selected.short_strike),
        long_strike=getattr(decision, "long_strike", selected.long_strike),
        entry_credit=getattr(decision, "limit_credit", selected.credit),
    )
    return executor.submit_limit_combo(
        position,
        limit_price=getattr(decision, "limit_credit", selected.credit),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trader-shawn")
    subparsers = parser.add_subparsers(dest="command", required=True)

    trade_cycle = subparsers.add_parser("trade-cycle", help="Run one trade cycle")
    trade_cycle.set_defaults(handler=_trade_cycle_command)

    dashboard = subparsers.add_parser("dashboard", help="Print dashboard status")
    dashboard.add_argument("state_path", help="Path to the persisted dashboard state JSON file")
    dashboard.set_defaults(handler=_dashboard_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = args.handler(args)
    if result is not None:
        print(json.dumps(result, sort_keys=True))
    return 0


def _trade_cycle_command(_: argparse.Namespace) -> dict[str, str]:
    return {"status": "not_implemented"}


def _dashboard_command(args: argparse.Namespace) -> dict[str, Any]:
    from trader_shawn.monitoring.dashboard_api import read_dashboard_snapshot

    return read_dashboard_snapshot(args.state_path)


if __name__ == "__main__":
    raise SystemExit(main())
