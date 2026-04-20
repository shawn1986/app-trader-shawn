from __future__ import annotations

from trader_shawn.domain.models import CandidateSpread, PositionSnapshot
from trader_shawn.execution.order_builder import (
    build_credit_spread_combo_order,
    build_open_credit_spread_combo_order,
)


class IbkrExecutor:
    def submit_open_credit_spread(
        self,
        spread: CandidateSpread,
        *,
        limit_credit: float,
        quantity: int = 1,
    ) -> dict[str, object]:
        return {
            "status": "stubbed",
            "broker": "ibkr",
            "order": build_open_credit_spread_combo_order(
                spread,
                limit_credit=limit_credit,
                quantity=quantity,
            ),
        }

    def submit_limit_combo(
        self,
        position: PositionSnapshot,
        *,
        limit_price: float,
    ) -> dict[str, object]:
        return {
            "status": "stubbed",
            "broker": "ibkr",
            "order": build_credit_spread_combo_order(
                position,
                limit_price=limit_price,
            ),
        }
