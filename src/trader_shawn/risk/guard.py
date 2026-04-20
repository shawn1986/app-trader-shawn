from __future__ import annotations

from trader_shawn.domain.models import AccountSnapshot, CandidateSpread
from trader_shawn.risk.rules import GuardResult
from trader_shawn.settings import RiskSettings


class RiskGuard:
    def __init__(self, risk_settings: RiskSettings) -> None:
        self._risk_settings = risk_settings

    def evaluate(
        self,
        spread: CandidateSpread,
        account: AccountSnapshot,
        open_symbol_count: int,
    ) -> GuardResult:
        if spread.max_loss > account.net_liq * self._risk_settings.max_risk_per_trade_pct:
            return GuardResult(allowed=False, reason="max_risk_per_trade_pct")
        if (
            abs(account.realized_pnl + account.unrealized_pnl)
            > account.net_liq * self._risk_settings.max_daily_loss_pct
        ):
            return GuardResult(allowed=False, reason="max_daily_loss_pct")
        if account.new_positions_today >= self._risk_settings.max_new_positions_per_day:
            return GuardResult(allowed=False, reason="max_new_positions_per_day")
        if account.open_risk > account.net_liq * self._risk_settings.max_open_risk_pct:
            return GuardResult(allowed=False, reason="max_open_risk_pct")
        if open_symbol_count >= self._risk_settings.max_spreads_per_symbol:
            return GuardResult(allowed=False, reason="max_spreads_per_symbol")
        return GuardResult(allowed=True, reason="ok")
