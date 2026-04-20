from __future__ import annotations

import math

from trader_shawn.domain.enums import PositionSide
from trader_shawn.domain.models import CandidateSpread, PositionSnapshot


def _option_right_for_strategy(strategy: str) -> str:
    normalized = strategy.lower()
    if normalized == "bull_put_credit_spread":
        return "P"
    if normalized == "bear_call_credit_spread":
        return "C"
    raise ValueError(f"unsupported credit spread strategy: {strategy}")


def _validate_strike_order(strategy: str, short_strike: float, long_strike: float) -> None:
    normalized = strategy.lower()
    if normalized == "bull_put_credit_spread" and short_strike <= long_strike:
        raise ValueError("short_strike must be greater than long_strike")
    if normalized == "bear_call_credit_spread" and short_strike >= long_strike:
        raise ValueError("short_strike must be less than long_strike")


def _validate_limit_value(value: float, *, field_name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field_name} must be a positive finite number")


def _validate_positive_quantity(quantity: int) -> None:
    if quantity <= 0:
        raise ValueError("quantity must be positive")


def _validate_credit_spread_shape(
    strategy: str,
    short_strike: float | None,
    long_strike: float | None,
    expiry: str,
) -> None:
    if short_strike is None or long_strike is None:
        raise ValueError("credit spread combo order requires short and long strikes")
    _validate_strike_order(strategy, short_strike, long_strike)
    if not expiry:
        raise ValueError("credit spread combo order requires an expiry")


def _validate_credit_spread_close(position: PositionSnapshot) -> None:
    _validate_positive_quantity(position.quantity)
    _validate_credit_spread_shape(
        position.strategy,
        position.short_strike,
        position.long_strike,
        position.expiry,
    )
    if position.entry_credit is None or position.entry_credit <= 0:
        raise ValueError("snapshot must represent a short credit spread")
    if position.side is not None and position.side is not PositionSide.SHORT:
        raise ValueError("snapshot must represent a short credit spread")


def build_open_credit_spread_combo_order(
    spread: CandidateSpread,
    *,
    limit_credit: float,
    quantity: int = 1,
) -> dict[str, object]:
    _validate_positive_quantity(quantity)
    _validate_credit_spread_shape(
        spread.strategy,
        spread.short_strike,
        spread.long_strike,
        spread.expiry,
    )
    _validate_limit_value(limit_credit, field_name="limit_credit")
    right = _option_right_for_strategy(spread.strategy)

    return {
        "symbol": spread.ticker,
        "strategy": spread.strategy,
        "secType": "BAG",
        "currency": "USD",
        "exchange": "SMART",
        "legs": [
            {
                "action": "SELL",
                "ratio": 1,
                "right": right,
                "strike": spread.short_strike,
                "expiry": spread.expiry,
            },
            {
                "action": "BUY",
                "ratio": 1,
                "right": right,
                "strike": spread.long_strike,
                "expiry": spread.expiry,
            },
        ],
        "order": {
            "action": "SELL",
            "orderType": "LMT",
            "totalQuantity": quantity,
            "lmtPrice": limit_credit,
            "transmit": False,
        },
    }


def build_credit_spread_combo_order(
    position: PositionSnapshot,
    *,
    limit_price: float,
) -> dict[str, object]:
    _validate_credit_spread_close(position)
    right = _option_right_for_strategy(position.strategy)

    return {
        "symbol": position.ticker,
        "strategy": position.strategy,
        "secType": "BAG",
        "currency": "USD",
        "exchange": "SMART",
        "legs": [
            {
                "action": "BUY",
                "ratio": 1,
                "right": right,
                "strike": position.short_strike,
                "expiry": position.expiry,
            },
            {
                "action": "SELL",
                "ratio": 1,
                "right": right,
                "strike": position.long_strike,
                "expiry": position.expiry,
            },
        ],
        "order": {
            "action": "BUY",
            "orderType": "LMT",
            "totalQuantity": position.quantity,
            "lmtPrice": limit_price,
            "transmit": False,
        },
    }
