import pytest

from trader_shawn.domain.models import AccountSnapshot, CandidateSpread
from trader_shawn.risk.guard import RiskGuard
from trader_shawn.settings import RiskSettings


def _risk_settings() -> RiskSettings:
    return RiskSettings(
        max_risk_per_trade_pct=0.02,
        max_daily_loss_pct=0.04,
        max_new_positions_per_day=6,
        max_open_risk_pct=0.20,
        max_spreads_per_symbol=2,
        profit_take_pct=0.50,
        stop_loss_multiple=2.0,
        exit_dte_threshold=5,
    )


def _guard() -> RiskGuard:
    return RiskGuard(_risk_settings())


def _account(
    *,
    net_liq: float = 10_000,
    realized_pnl: float = 0,
    unrealized_pnl: float = 0,
    open_risk: float = 500,
    new_positions_today: int = 0,
) -> AccountSnapshot:
    return AccountSnapshot(
        net_liq=net_liq,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        open_risk=open_risk,
        new_positions_today=new_positions_today,
    )


def _spread(*, max_loss: float = 150) -> CandidateSpread:
    return CandidateSpread(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        dte=10,
        short_strike=160,
        long_strike=155,
        width=5,
        credit=1.0,
        max_loss=max_loss,
        short_delta=0.20,
        pop=0.80,
        bid_ask_ratio=0.08,
    )


def test_risk_guard_constructs_from_risk_settings() -> None:
    guard = RiskGuard(_risk_settings())

    result = guard.evaluate(_spread(), _account(), open_symbol_count=0)

    assert result.allowed is True
    assert result.reason == "ok"


def test_risk_guard_allows_trade_when_all_limits_are_within_thresholds() -> None:
    result = _guard().evaluate(_spread(), _account(), open_symbol_count=1)

    assert result.allowed is True
    assert result.reason == "ok"


@pytest.mark.parametrize(
    (
        "max_loss",
        "net_liq",
        "realized_pnl",
        "unrealized_pnl",
        "open_risk",
        "new_positions_today",
        "open_symbol_count",
        "expected_reason",
    ),
    [
        (201, 10_000, 0, 0, 500, 0, 0, "max_risk_per_trade_pct"),
        (150, 9_600, -401, 0, 500, 0, 0, "max_daily_loss_pct"),
        (150, 10_000, 0, 0, 500, 6, 0, "max_new_positions_per_day"),
        (150, 10_000, 0, 0, 1_851, 0, 0, "max_open_risk_pct"),
        (150, 10_000, 0, 0, 500, 0, 2, "max_spreads_per_symbol"),
    ],
)
def test_risk_guard_rejects_each_rule(
    max_loss: float,
    net_liq: float,
    realized_pnl: float,
    unrealized_pnl: float,
    open_risk: float,
    new_positions_today: int,
    open_symbol_count: int,
    expected_reason: str,
) -> None:
    result = _guard().evaluate(
        _spread(max_loss=max_loss),
        _account(
            net_liq=net_liq,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            open_risk=open_risk,
            new_positions_today=new_positions_today,
        ),
        open_symbol_count=open_symbol_count,
    )

    assert result.allowed is False
    assert result.reason == expected_reason


@pytest.mark.parametrize(
    (
        "max_loss",
        "net_liq",
        "realized_pnl",
        "unrealized_pnl",
        "open_risk",
        "new_positions_today",
        "open_symbol_count",
    ),
    [
        (200, 10_000, 0, 0, 500, 0, 0),
        (150, 9_600, -400, 0, 500, 0, 0),
        (150, 10_000, 0, 0, 500, 5, 0),
        (150, 10_000, 0, 0, 1_850, 0, 0),
        (150, 10_000, 0, 0, 500, 0, 1),
    ],
)
def test_risk_guard_allows_when_value_equals_threshold(
    max_loss: float,
    net_liq: float,
    realized_pnl: float,
    unrealized_pnl: float,
    open_risk: float,
    new_positions_today: int,
    open_symbol_count: int,
) -> None:
    result = _guard().evaluate(
        _spread(max_loss=max_loss),
        _account(
            net_liq=net_liq,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            open_risk=open_risk,
            new_positions_today=new_positions_today,
        ),
        open_symbol_count=open_symbol_count,
    )

    assert result.allowed is True
    assert result.reason == "ok"


def test_risk_guard_ignores_large_gains_for_daily_loss_limit() -> None:
    result = _guard().evaluate(
        _spread(),
        _account(realized_pnl=500, unrealized_pnl=100),
        open_symbol_count=0,
    )

    assert result.allowed is True
    assert result.reason == "ok"


def test_risk_guard_uses_pre_loss_equity_for_daily_loss_limit() -> None:
    result = _guard().evaluate(
        _spread(),
        _account(net_liq=9_610, realized_pnl=-390, unrealized_pnl=0),
        open_symbol_count=0,
    )

    assert result.allowed is True
    assert result.reason == "ok"


def test_risk_guard_returns_first_matching_rejection_reason() -> None:
    result = _guard().evaluate(
        _spread(max_loss=500),
        _account(realized_pnl=-600, open_risk=3000, new_positions_today=10),
        open_symbol_count=5,
    )

    assert result.allowed is False
    assert result.reason == "max_risk_per_trade_pct"


def test_risk_guard_rejects_when_candidate_trade_pushes_open_risk_over_limit() -> None:
    result = _guard().evaluate(
        _spread(max_loss=101),
        _account(open_risk=1_900),
        open_symbol_count=0,
    )

    assert result.allowed is False
    assert result.reason == "max_open_risk_pct"


def test_risk_guard_allows_when_candidate_trade_brings_open_risk_to_limit() -> None:
    result = _guard().evaluate(
        _spread(max_loss=100),
        _account(open_risk=1_900),
        open_symbol_count=0,
    )

    assert result.allowed is True
    assert result.reason == "ok"


def test_account_snapshot_accepts_net_liq_alias() -> None:
    account = AccountSnapshot(net_liq=12_345)

    assert account.net_liq == 12_345
    assert account.net_liquidation == 12_345


def test_account_snapshot_accepts_net_liquidation_name() -> None:
    account = AccountSnapshot(net_liquidation=54_321)

    assert account.net_liq == 54_321
    assert account.net_liquidation == 54_321


def test_account_snapshot_prefers_net_liq_when_both_aliases_are_provided() -> None:
    account = AccountSnapshot(net_liq=12_345, net_liquidation=54_321)

    assert account.net_liq == 12_345
    assert account.net_liquidation == 12_345
