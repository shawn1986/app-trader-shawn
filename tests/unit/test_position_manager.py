from trader_shawn.domain.models import PositionSnapshot
from trader_shawn.execution.ibkr_executor import IbkrExecutor
from trader_shawn.execution.order_builder import build_credit_spread_combo_order
from trader_shawn.positions.manager import evaluate_exit


def test_evaluate_exit_returns_take_profit_when_debit_reaches_half_credit() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=1.00,
        current_debit=0.45,
        dte=9,
        short_leg_distance_pct=0.08,
    )

    assert (
        evaluate_exit(
            position,
            profit_take_pct=0.50,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        )
        == "take_profit"
    )


def test_evaluate_exit_returns_stop_loss_when_debit_doubles_credit() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=1.00,
        current_debit=2.05,
        dte=9,
        short_leg_distance_pct=0.08,
    )

    assert (
        evaluate_exit(
            position,
            profit_take_pct=0.50,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        )
        == "stop_loss"
    )


def test_evaluate_exit_returns_dte_exit_when_days_to_expiration_hits_threshold() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=1.00,
        current_debit=0.80,
        dte=5,
        short_leg_distance_pct=0.08,
    )

    assert (
        evaluate_exit(
            position,
            profit_take_pct=0.50,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        )
        == "dte_exit"
    )


def test_evaluate_exit_returns_short_strike_proximity_when_under_distance_threshold() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=1.00,
        current_debit=0.80,
        dte=9,
        short_leg_distance_pct=0.01,
    )

    assert (
        evaluate_exit(
            position,
            profit_take_pct=0.50,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
            short_strike_distance_threshold_pct=0.02,
        )
        == "short_strike_proximity"
    )


def test_build_credit_spread_combo_order_creates_buy_to_close_payload() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=1.00,
        current_debit=0.45,
        dte=9,
        short_leg_distance_pct=0.08,
        quantity=2,
    )

    assert build_credit_spread_combo_order(position, limit_price=0.45) == {
        "symbol": "AMD",
        "strategy": "bull_put_credit_spread",
        "secType": "BAG",
        "currency": "USD",
        "exchange": "SMART",
        "legs": [
            {
                "action": "BUY",
                "ratio": 1,
                "right": "P",
                "strike": 160,
                "expiry": "2026-04-30",
            },
            {
                "action": "SELL",
                "ratio": 1,
                "right": "P",
                "strike": 155,
                "expiry": "2026-04-30",
            },
        ],
        "order": {
            "action": "BUY",
            "orderType": "LMT",
            "totalQuantity": 2,
            "lmtPrice": 0.45,
            "transmit": False,
        },
    }


def test_ibkr_executor_submit_limit_combo_returns_stubbed_submission_record() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=1.00,
        current_debit=0.45,
        dte=9,
        short_leg_distance_pct=0.08,
    )
    executor = IbkrExecutor()

    result = executor.submit_limit_combo(position, limit_price=0.45)

    assert result == {
        "status": "stubbed",
        "broker": "ibkr",
        "order": build_credit_spread_combo_order(position, limit_price=0.45),
    }
