from __future__ import annotations

from trader_shawn.domain.models import PositionSnapshot


def _option_right_for_strategy(strategy: str) -> str:
    normalized = strategy.lower()
    if "put" in normalized:
        return "P"
    if "call" in normalized:
        return "C"
    raise ValueError(f"unsupported credit spread strategy: {strategy}")


def build_credit_spread_combo_order(
    position: PositionSnapshot,
    *,
    limit_price: float,
) -> dict[str, object]:
    if position.short_strike is None or position.long_strike is None:
        raise ValueError("credit spread combo order requires short and long strikes")
    if not position.expiry:
        raise ValueError("credit spread combo order requires an expiry")

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
