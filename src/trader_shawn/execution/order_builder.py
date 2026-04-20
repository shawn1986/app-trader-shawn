from __future__ import annotations

from trader_shawn.domain.enums import PositionSide
from trader_shawn.domain.models import PositionSnapshot


def _option_right_for_strategy(strategy: str) -> str:
    normalized = strategy.lower()
    if normalized == "bull_put_credit_spread":
        return "P"
    if normalized == "bear_call_credit_spread":
        return "C"
    raise ValueError(f"unsupported credit spread strategy: {strategy}")


def _validate_credit_spread_close(position: PositionSnapshot) -> None:
    if position.quantity <= 0:
        raise ValueError("quantity must be positive")
    if position.short_strike is None or position.long_strike is None:
        raise ValueError("credit spread combo order requires short and long strikes")
    if not position.expiry:
        raise ValueError("credit spread combo order requires an expiry")
    if position.entry_credit is None or position.entry_credit <= 0:
        raise ValueError("snapshot must represent a short credit spread")
    if position.side is not None and position.side is not PositionSide.SHORT:
        raise ValueError("snapshot must represent a short credit spread")


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
