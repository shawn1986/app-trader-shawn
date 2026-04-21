from __future__ import annotations

from typing import Any

from trader_shawn.app import run_trade_cycle


def run_scheduled_trade_cycle(**kwargs: Any) -> dict[str, Any]:
    return run_trade_cycle(**kwargs)
