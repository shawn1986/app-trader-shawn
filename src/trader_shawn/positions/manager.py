from __future__ import annotations

from trader_shawn.domain.models import PositionSnapshot


def evaluate_exit(
    position: PositionSnapshot,
    *,
    profit_take_pct: float,
    stop_loss_multiple: float,
    exit_dte_threshold: int,
    short_strike_distance_threshold_pct: float = 0.02,
) -> str | None:
    if position.entry_credit <= 0:
        return None

    if position.current_debit <= position.entry_credit * (1 - profit_take_pct):
        return "take_profit"

    if position.current_debit >= position.entry_credit * stop_loss_multiple:
        return "stop_loss"

    if position.dte <= exit_dte_threshold:
        return "dte_exit"

    if (
        position.short_leg_distance_pct is not None
        and position.short_leg_distance_pct <= short_strike_distance_threshold_pct
    ):
        return "short_strike_proximity"

    return None
