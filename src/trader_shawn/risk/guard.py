from __future__ import annotations

from trader_shawn.domain.models import AccountSnapshot, CandidateSpread
from trader_shawn.risk.rules import GuardResult


class RiskGuard:
    def __init__(
        self,
        max_risk_per_trade_pct: float,
        max_daily_loss_pct: float,
        max_new_positions_per_day: int,
        max_open_risk_pct: float,
        max_spreads_per_symbol: int,
    ) -> None:
        self._max_risk_per_trade_pct = max_risk_per_trade_pct
        self._max_daily_loss_pct = max_daily_loss_pct
        self._max_new_positions_per_day = max_new_positions_per_day
        self._max_open_risk_pct = max_open_risk_pct
        self._max_spreads_per_symbol = max_spreads_per_symbol

    def evaluate(
        self,
        spread: CandidateSpread,
        account: AccountSnapshot,
        open_symbol_count: int,
    ) -> GuardResult:
        if spread.max_loss > account.net_liq * self._max_risk_per_trade_pct:
            return GuardResult(allowed=False, reason="max_risk_per_trade_pct")
        if (
            abs(account.realized_pnl + account.unrealized_pnl)
            > account.net_liq * self._max_daily_loss_pct
        ):
            return GuardResult(allowed=False, reason="max_daily_loss_pct")
        if account.new_positions_today >= self._max_new_positions_per_day:
            return GuardResult(allowed=False, reason="max_new_positions_per_day")
        if account.open_risk > account.net_liq * self._max_open_risk_pct:
            return GuardResult(allowed=False, reason="max_open_risk_pct")
        if open_symbol_count >= self._max_spreads_per_symbol:
            return GuardResult(allowed=False, reason="max_spreads_per_symbol")
        return GuardResult(allowed=True, reason="ok")
