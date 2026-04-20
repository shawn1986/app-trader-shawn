from trader_shawn.domain.models import AccountSnapshot, CandidateSpread
from trader_shawn.risk.guard import RiskGuard
from trader_shawn.settings import RiskSettings


def test_risk_guard_blocks_when_trade_risk_exceeds_limit() -> None:
    risk_settings = RiskSettings(
        max_risk_per_trade_pct=0.02,
        max_daily_loss_pct=0.04,
        max_new_positions_per_day=6,
        max_open_risk_pct=0.20,
        max_spreads_per_symbol=2,
        profit_take_pct=0.50,
        stop_loss_multiple=2.0,
        exit_dte_threshold=5,
    )
    guard = RiskGuard(risk_settings)
    account = AccountSnapshot(
        net_liq=10_000,
        realized_pnl=0,
        unrealized_pnl=0,
        open_risk=500,
        new_positions_today=0,
    )
    spread = CandidateSpread(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        dte=10,
        short_strike=160,
        long_strike=150,
        width=10,
        credit=1.0,
        max_loss=900,
        short_delta=0.20,
        pop=0.80,
        bid_ask_ratio=0.08,
    )

    result = guard.evaluate(spread, account, open_symbol_count=0)

    assert result.allowed is False
    assert result.reason == "max_risk_per_trade_pct"
