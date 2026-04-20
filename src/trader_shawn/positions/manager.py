from __future__ import annotations

from trader_shawn.domain.models import PositionSnapshot


def _require_exit_field(position: PositionSnapshot, field_name: str) -> float | int:
    value = getattr(position, field_name)
    if value is None:
        raise ValueError(f"missing required exit field: {field_name}")
    return value


def evaluate_exit(
    position: PositionSnapshot,
    *,
    profit_take_pct: float,
    stop_loss_multiple: float,
    exit_dte_threshold: int,
    short_strike_distance_threshold_pct: float = 0.02,
) -> str | None:
    entry_credit = _require_exit_field(position, "entry_credit")
    current_debit = _require_exit_field(position, "current_debit")
    dte = _require_exit_field(position, "dte")

    if entry_credit <= 0:
        return None

    if current_debit <= entry_credit * (1 - profit_take_pct):
        return "take_profit"

    if current_debit >= entry_credit * stop_loss_multiple:
        return "stop_loss"

    if dte <= exit_dte_threshold:
        return "dte_exit"

    if (
        position.short_leg_distance_pct is not None
        and position.short_leg_distance_pct <= short_strike_distance_threshold_pct
    ):
        return "short_strike_proximity"

    return None
