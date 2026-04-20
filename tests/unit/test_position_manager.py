from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from trader_shawn.domain.models import (
    BrokerOptionPosition,
    ManagedPositionRecord,
    PositionSnapshot,
)
from trader_shawn.domain.enums import PositionSide
from trader_shawn.events.earnings_calendar import EarningsCalendar
from trader_shawn.monitoring import audit_logger as audit_logger_module
from trader_shawn.monitoring.audit_logger import AuditLogger
from trader_shawn.execution.ibkr_executor import IbkrExecutor, OrderNotSubmittedError
from trader_shawn.execution.order_builder import build_credit_spread_combo_order
from trader_shawn.positions.manager import PositionManager, evaluate_exit
from datetime import UTC, date, datetime


def test_audit_logger_persists_and_updates_managed_position(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.db")

    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "initial",
        }
    )
    logger.record_position_event(
        "pos-1",
        "opened",
        {"entry_order_id": 321},
    )
    logger.update_managed_position(
        "pos-1",
        status="closing",
        last_known_debit=0.52,
        last_evaluated_at="2026-04-20T10:00:00+00:00",
    )

    positions = logger.fetch_active_managed_positions(mode="paper")
    events = logger.fetch_position_events("pos-1")

    assert positions == [
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "closing",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": 0.52,
            "last_evaluated_at": "2026-04-20T10:00:00+00:00",
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "initial",
        }
    ]
    assert events[0]["event_type"] == "opened"
    assert events[0]["payload"] == {"entry_order_id": 321}


def test_audit_logger_rejects_orphan_position_event(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.db")

    with pytest.raises(sqlite3.IntegrityError):
        logger.record_position_event(
            "missing-position",
            "opened",
            {"entry_order_id": 321},
        )


def test_fetch_active_managed_positions_only_returns_open_and_closing(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")

    logger.upsert_managed_position(
        ManagedPositionRecord(
            position_id="pos-open",
            ticker="AMD",
            strategy="bull_put_credit_spread",
            expiry="2026-04-30",
            short_strike=160.0,
            long_strike=155.0,
            quantity=1,
            entry_credit=1.05,
            entry_order_id=321,
            mode="paper",
            status="open",
            opened_at=datetime(2026, 4, 20, 9, 31, tzinfo=UTC),
            last_evaluated_at=datetime(2026, 4, 20, 10, 0, tzinfo=UTC),
            broker_fingerprint="AMD|2026-04-30|P|160.0|155.0|1",
            decision_reason="opened by system",
            risk_note="initial",
        )
    )
    logger.upsert_managed_position(
        {
            "position_id": "pos-closing",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 322,
            "mode": "paper",
            "status": "closing",
            "opened_at": "2026-04-20T09:32:00+00:00",
            "closed_at": None,
            "last_known_debit": 0.55,
            "last_evaluated_at": "2026-04-20T10:05:00+00:00",
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|2",
            "decision_reason": "close in progress",
            "risk_note": "watch fill",
        }
    )
    for position_id, status in (
        ("pos-closed", "closed"),
        ("pos-cancelled", "cancelled"),
    ):
        logger.upsert_managed_position(
            {
                "position_id": position_id,
                "ticker": "AMD",
                "strategy": "bull_put_credit_spread",
                "expiry": "2026-04-30",
                "short_strike": 160.0,
                "long_strike": 155.0,
                "quantity": 1,
                "entry_credit": 1.05,
                "entry_order_id": 323,
                "mode": "paper",
                "status": status,
                "opened_at": "2026-04-20T09:33:00+00:00",
                "closed_at": None,
                "last_known_debit": None,
                "last_evaluated_at": None,
                "broker_fingerprint": f"AMD|2026-04-30|P|160.0|155.0|{status}",
                "decision_reason": status,
                "risk_note": status,
            }
        )

    positions = logger.fetch_active_managed_positions(mode="paper")

    assert [position["position_id"] for position in positions] == [
        "pos-open",
        "pos-closing",
    ]
    assert positions[0]["opened_at"] == "2026-04-20T09:31:00+00:00"
    assert positions[0]["last_evaluated_at"] == "2026-04-20T10:00:00+00:00"


def test_audit_logger_closes_sqlite_connections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened_connection_ids: list[int] = []
    closed_connection_ids: list[int] = []
    original_connect = sqlite3.connect

    class TrackingConnection(sqlite3.Connection):
        def close(self) -> None:
            closed_connection_ids.append(id(self))
            super().close()

    def tracking_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs, factory=TrackingConnection)
        opened_connection_ids.append(id(connection))
        return connection

    monkeypatch.setattr(audit_logger_module.sqlite3, "connect", tracking_connect)

    logger = AuditLogger(tmp_path / "audit.db")
    logger.fetch_active_managed_positions(mode="paper")

    assert opened_connection_ids
    assert closed_connection_ids == opened_connection_ids


def test_audit_logger_uses_payload_json_column_for_position_events(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "",
        }
    )
    logger.record_position_event(
        "pos-1",
        "opened",
        {"entry_order_id": 321},
    )

    with sqlite3.connect(tmp_path / "audit.db") as connection:
        columns = {
            row[1]
            for row in connection.execute("pragma table_info(position_events)").fetchall()
        }
        stored_payload = connection.execute(
            "select payload_json from position_events where position_id = ?",
            ("pos-1",),
        ).fetchone()[0]

    assert "payload_json" in columns
    assert "payload" not in columns
    assert stored_payload == '{"entry_order_id": 321}'
    assert logger.fetch_position_events("pos-1")[0]["payload"] == {"entry_order_id": 321}


def test_update_managed_position_if_status_only_updates_when_expected_status_matches(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "",
        }
    )

    claimed = logger.update_managed_position_if_status(
        "pos-1",
        expected_status="open",
        status="closing",
        last_known_debit=0.42,
    )
    rejected = logger.update_managed_position_if_status(
        "pos-1",
        expected_status="open",
        status="open",
        last_known_debit=0.99,
    )

    position = logger.fetch_active_managed_positions(mode="paper")[0]
    assert claimed is True
    assert rejected is False
    assert position["status"] == "closing"
    assert position["last_known_debit"] == 0.42


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


def test_evaluate_exit_returns_event_risk_exit_when_earnings_is_before_expiry() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=1.00,
        current_debit=0.80,
        dte=9,
        short_leg_distance_pct=0.08,
    )
    calendar = EarningsCalendar([{"ticker": "AMD", "date": date(2026, 4, 28)}])

    assert (
        evaluate_exit(
            position,
            profit_take_pct=0.50,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
            earnings_calendar=calendar,
            as_of=date(2026, 4, 20),
        )
        == "event_risk_exit"
    )


def test_evaluate_exit_raises_for_incomplete_exit_snapshot() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=None,
        current_debit=0.45,
        dte=9,
        short_leg_distance_pct=0.08,
    )

    with pytest.raises(ValueError, match="missing required exit field: entry_credit"):
        evaluate_exit(
            position,
            profit_take_pct=0.50,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        )


def test_evaluate_exit_raises_for_partial_exit_snapshot_even_if_other_rules_match() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=1.00,
        current_debit=0.45,
        dte=None,
        short_leg_distance_pct=0.01,
    )

    with pytest.raises(ValueError, match="missing required exit field: dte"):
        evaluate_exit(
            position,
            profit_take_pct=0.50,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        )


def test_evaluate_exit_prefers_take_profit_when_multiple_exit_rules_are_true() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=1.00,
        current_debit=0.45,
        dte=5,
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
        == "take_profit"
    )


def test_evaluate_exit_prefers_stop_loss_over_later_rules() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=1.00,
        current_debit=2.20,
        dte=5,
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
        == "stop_loss"
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


def test_build_credit_spread_combo_order_creates_call_spread_close_payload() -> None:
    position = PositionSnapshot(
        ticker="NVDA",
        strategy="bear_call_credit_spread",
        expiry="2026-05-15",
        short_strike=125,
        long_strike=130,
        entry_credit=1.20,
        current_debit=0.60,
        dte=11,
        short_leg_distance_pct=0.07,
        quantity=1,
    )

    assert build_credit_spread_combo_order(position, limit_price=0.60) == {
        "symbol": "NVDA",
        "strategy": "bear_call_credit_spread",
        "secType": "BAG",
        "currency": "USD",
        "exchange": "SMART",
        "legs": [
            {
                "action": "BUY",
                "ratio": 1,
                "right": "C",
                "strike": 125,
                "expiry": "2026-05-15",
            },
            {
                "action": "SELL",
                "ratio": 1,
                "right": "C",
                "strike": 130,
                "expiry": "2026-05-15",
            },
        ],
        "order": {
            "action": "BUY",
            "orderType": "LMT",
            "totalQuantity": 1,
            "lmtPrice": 0.60,
            "transmit": False,
        },
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"strategy": "iron_condor"}, "unsupported credit spread strategy"),
        ({"quantity": 0}, "quantity must be positive"),
        ({"expiry": ""}, "requires an expiry"),
        ({"short_strike": None}, "requires short and long strikes"),
        ({"long_strike": None}, "requires short and long strikes"),
        ({"side": "long"}, "must represent a short credit spread"),
        ({"entry_credit": None}, "must represent a short credit spread"),
    ],
)
def test_build_credit_spread_combo_order_rejects_invalid_inputs(
    overrides: dict[str, object],
    message: str,
) -> None:
    payload = {
        "ticker": "AMD",
        "strategy": "bull_put_credit_spread",
        "expiry": "2026-04-30",
        "short_strike": 160,
        "long_strike": 155,
        "entry_credit": 1.00,
        "current_debit": 0.45,
        "dte": 9,
        "short_leg_distance_pct": 0.08,
        "quantity": 2,
    }
    payload.update(overrides)
    position = PositionSnapshot(**payload)

    with pytest.raises(ValueError, match=message):
        build_credit_spread_combo_order(position, limit_price=0.45)


@pytest.mark.parametrize(
    ("strategy", "short_strike", "long_strike", "message"),
    [
        (
            "bull_put_credit_spread",
            155,
            160,
            "short_strike must be greater than long_strike",
        ),
        (
            "bear_call_credit_spread",
            130,
            125,
            "short_strike must be less than long_strike",
        ),
    ],
)
def test_build_credit_spread_combo_order_rejects_swapped_strikes_by_strategy(
    strategy: str,
    short_strike: int,
    long_strike: int,
    message: str,
) -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy=strategy,
        expiry="2026-04-30",
        short_strike=short_strike,
        long_strike=long_strike,
        entry_credit=1.00,
        current_debit=0.45,
        dte=9,
        short_leg_distance_pct=0.08,
        quantity=1,
    )

    with pytest.raises(ValueError, match=message):
        build_credit_spread_combo_order(position, limit_price=0.45)


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
    executor = IbkrExecutor(
        client=_FakeIbClient(),
        ibkr_module=_FakeIbModule(),
    )

    result = executor.submit_limit_combo(position, limit_price=0.45)

    assert result == {
        "status": "submitted",
        "broker": "ibkr",
        "order_id": 404,
        "order": {
            "action": "BUY",
            "orderType": "LMT",
            "totalQuantity": 1,
            "lmtPrice": 0.45,
            "transmit": True,
        },
        "contract": {
            "symbol": "AMD",
            "secType": "BAG",
            "currency": "USD",
            "exchange": "SMART",
        },
        "legs": [
            {
                "con_id": 50160,
                "action": "BUY",
                "ratio": 1,
                "exchange": "SMART",
                "right": "P",
                "strike": 160.0,
                "expiry": "2026-04-30",
            },
            {
                "con_id": 50155,
                "action": "SELL",
                "ratio": 1,
                "exchange": "SMART",
                "right": "P",
                "strike": 155.0,
                "expiry": "2026-04-30",
            },
        ],
        "broker_status": "PendingSubmit",
        "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
    }


def test_ibkr_executor_submit_limit_combo_raises_order_not_submitted_error_when_qualification_fails() -> None:
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
    executor = IbkrExecutor(
        client=_FailingQualificationIbClient(),
        ibkr_module=_FakeIbModule(),
    )

    with pytest.raises(OrderNotSubmittedError, match="failed to qualify all combo legs"):
        executor.submit_limit_combo(position, limit_price=0.45)


def test_manage_positions_fails_closed_on_unknown_broker_option_position(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
            ]
        ),
        executor=FakeManageExecutor(),
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
    )

    result = manager.manage_positions()

    assert result["status"] == "anomaly"
    assert result["reason"] == "unknown_broker_position"


def test_manage_positions_submits_close_for_take_profit(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "",
        }
    )
    executor = FakeManageExecutor()
    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
            ],
            spread_debit=0.42,
            spot_price=171.0,
        ),
        executor=executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = manager.manage_positions()

    assert result == {
        "status": "submitted",
        "position_id": "pos-1",
        "ticker": "AMD",
        "exit_reason": "take_profit",
        "payload": {
            "status": "submitted",
            "order_id": 999,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "limit_price": 0.42,
        },
    }
    assert executor.calls == [("AMD", 0.42)]
    assert executor.positions[0].side is PositionSide.SHORT
    assert logger.fetch_active_managed_positions(mode="paper")[0]["status"] == "closing"
    assert logger.fetch_position_events("pos-1")[-1]["event_type"] == "close_submitted"


def test_manage_positions_fails_closed_when_unknown_broker_position_exists_alongside_matching_managed_position(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "",
        }
    )
    executor = FakeManageExecutor()
    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
                BrokerOptionPosition(
                    ticker="NVDA",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=110.0,
                    broker_position_id="90110",
                ),
                BrokerOptionPosition(
                    ticker="NVDA",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=105.0,
                    broker_position_id="90105",
                ),
            ],
            spread_debit=0.42,
            spot_price=171.0,
        ),
        executor=executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = manager.manage_positions()

    assert result == {
        "status": "anomaly",
        "reason": "unknown_broker_position",
        "fingerprints": ["NVDA|2026-04-30|P|110.0|105.0|1"],
    }
    assert executor.calls == []
    assert logger.fetch_active_managed_positions(mode="paper")[0]["status"] == "open"
    assert logger.fetch_position_events("pos-1") == []


def test_manage_positions_returns_all_missing_fingerprints_when_broker_positions_are_missing(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    for position_id, fingerprint in (
        ("pos-1", "AMD|2026-04-30|P|160.0|155.0|1"),
        ("pos-2", "NVDA|2026-05-15|C|125.0|130.0|1"),
    ):
        logger.upsert_managed_position(
            {
                "position_id": position_id,
                "ticker": "AMD" if position_id == "pos-1" else "NVDA",
                "strategy": (
                    "bull_put_credit_spread"
                    if position_id == "pos-1"
                    else "bear_call_credit_spread"
                ),
                "expiry": "2026-04-30" if position_id == "pos-1" else "2026-05-15",
                "short_strike": 160.0 if position_id == "pos-1" else 125.0,
                "long_strike": 155.0 if position_id == "pos-1" else 130.0,
                "quantity": 1,
                "entry_credit": 1.05,
                "entry_order_id": 321,
                "mode": "paper",
                "status": "open",
                "opened_at": "2026-04-20T09:31:00+00:00",
                "closed_at": None,
                "last_known_debit": None,
                "last_evaluated_at": None,
                "broker_fingerprint": fingerprint,
                "decision_reason": "opened by system",
                "risk_note": "",
            }
        )

    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(option_positions=[]),
        executor=FakeManageExecutor(),
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = manager.manage_positions()

    assert result == {
        "status": "anomaly",
        "reason": "missing_broker_position",
        "fingerprints": [
            "AMD|2026-04-30|P|160.0|155.0|1",
            "NVDA|2026-05-15|C|125.0|130.0|1",
        ],
    }


def test_manage_positions_updates_evaluated_position_when_no_exit_triggers(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "",
        }
    )
    executor = FakeManageExecutor()
    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
            ],
            spread_debit=0.80,
            spot_price=180.0,
        ),
        executor=executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = manager.manage_positions()

    position = logger.fetch_active_managed_positions(mode="paper")[0]
    assert result == {"status": "ok", "managed_count": 1}
    assert executor.calls == []
    assert position["status"] == "open"
    assert position["last_known_debit"] == 0.80
    assert position["last_evaluated_at"] is not None
    assert logger.fetch_position_events("pos-1") == []


def test_manage_positions_refreshes_closing_position_when_no_submit_occurs(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "closing",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "closing in progress",
            "risk_note": "",
        }
    )
    executor = FakeManageExecutor()
    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
            ],
            spread_debit=0.61,
            spot_price=171.0,
        ),
        executor=executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = manager.manage_positions()

    position = logger.fetch_active_managed_positions(mode="paper")[0]
    assert result == {"status": "ok", "managed_count": 1}
    assert executor.calls == []
    assert position["status"] == "closing"
    assert position["last_known_debit"] == 0.61
    assert position["last_evaluated_at"] is not None


def test_manage_positions_fails_closed_when_saved_fields_do_not_match_full_identity(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "NVDA",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "",
        }
    )
    executor = FakeManageExecutor()
    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
            ],
            spread_debit=0.42,
            spot_price=171.0,
        ),
        executor=executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = manager.manage_positions()

    assert result == {
        "status": "anomaly",
        "reason": "unknown_broker_position",
        "fingerprints": ["NVDA|2026-04-30|P|160.0|155.0|1"],
    }
    assert executor.calls == []
    position = logger.fetch_active_managed_positions(mode="paper")[0]
    assert position["last_known_debit"] is None
    assert position["last_evaluated_at"] is None


def test_manage_positions_fails_closed_when_saved_strategy_does_not_match_broker_identity(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bear_call_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "",
        }
    )
    executor = FakeManageExecutor()
    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
            ],
            spread_debit=0.42,
            spot_price=171.0,
        ),
        executor=executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = manager.manage_positions()

    assert result == {
        "status": "anomaly",
        "reason": "unknown_broker_position",
        "fingerprints": ["AMD|2026-04-30|C|160.0|155.0|1"],
    }
    assert executor.calls == []
    position = logger.fetch_active_managed_positions(mode="paper")[0]
    assert position["status"] == "open"
    assert position["last_known_debit"] is None
    assert position["last_evaluated_at"] is None


def test_manage_positions_does_not_persist_partial_progress_when_later_reconstruction_fails(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    for position_id, ticker in (("pos-1", "AMD"), ("pos-2", "NVDA")):
        logger.upsert_managed_position(
            {
                "position_id": position_id,
                "ticker": ticker,
                "strategy": (
                    "bull_put_credit_spread"
                    if ticker == "AMD"
                    else "bear_call_credit_spread"
                ),
                "expiry": "2026-04-30" if ticker == "AMD" else "2026-05-15",
                "short_strike": 160.0 if ticker == "AMD" else 125.0,
                "long_strike": 155.0 if ticker == "AMD" else 130.0,
                "quantity": 1,
                "entry_credit": 1.05,
                "entry_order_id": 321,
                "mode": "paper",
                "status": "open",
                "opened_at": (
                    "2026-04-20T09:31:00+00:00"
                    if ticker == "AMD"
                    else "2026-04-20T09:32:00+00:00"
                ),
                "closed_at": None,
                "last_known_debit": None,
                "last_evaluated_at": None,
                "broker_fingerprint": (
                    "AMD|2026-04-30|P|160.0|155.0|1"
                    if ticker == "AMD"
                    else "NVDA|2026-05-15|C|125.0|130.0|1"
                ),
                "decision_reason": "opened by system",
                "risk_note": "",
            }
        )

    manager = PositionManager(
        audit_logger=logger,
        market_data=FailingManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
                BrokerOptionPosition(
                    ticker="NVDA",
                    expiry="2026-05-15",
                    right="C",
                    quantity=-1,
                    short_strike=125.0,
                    broker_position_id="90125",
                ),
                BrokerOptionPosition(
                    ticker="NVDA",
                    expiry="2026-05-15",
                    right="C",
                    quantity=1,
                    short_strike=130.0,
                    broker_position_id="90130",
                ),
            ],
            spread_debit=0.80,
            spot_price=180.0,
            raise_on_ticker="NVDA",
        ),
        executor=FakeManageExecutor(),
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    with pytest.raises(RuntimeError, match="debit unavailable for NVDA"):
        manager.manage_positions()

    positions = logger.fetch_active_managed_positions(mode="paper")
    assert positions[0]["position_id"] == "pos-1"
    assert positions[0]["last_known_debit"] is None
    assert positions[0]["last_evaluated_at"] is None
    assert positions[1]["position_id"] == "pos-2"
    assert positions[1]["last_known_debit"] is None
    assert positions[1]["last_evaluated_at"] is None


def test_manage_positions_does_not_finalize_missing_closing_position_when_later_reconstruction_fails(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "audit.db"
    logger = AuditLogger(db_path)
    logger.upsert_managed_position(
        {
            "position_id": "pos-closing",
            "ticker": "NVDA",
            "strategy": "bear_call_credit_spread",
            "expiry": "2026-05-15",
            "short_strike": 125.0,
            "long_strike": 130.0,
            "quantity": 1,
            "entry_credit": 1.10,
            "entry_order_id": 322,
            "mode": "paper",
            "status": "closing",
            "opened_at": "2026-04-20T09:30:00+00:00",
            "closed_at": None,
            "last_known_debit": 0.55,
            "last_evaluated_at": "2026-04-20T10:05:00+00:00",
            "broker_fingerprint": "NVDA|2026-05-15|C|125.0|130.0|1",
            "decision_reason": "close in progress",
            "risk_note": "",
        }
    )
    logger.upsert_managed_position(
        {
            "position_id": "pos-open",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "",
        }
    )
    manager = PositionManager(
        audit_logger=logger,
        market_data=FailingManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
            ],
            spread_debit=0.80,
            spot_price=180.0,
            raise_on_ticker="AMD",
        ),
        executor=FakeManageExecutor(),
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    with pytest.raises(RuntimeError, match="debit unavailable for AMD"):
        manager.manage_positions()

    active_positions = logger.fetch_active_managed_positions(mode="paper")
    assert [position["position_id"] for position in active_positions] == [
        "pos-closing",
        "pos-open",
    ]
    assert active_positions[0]["status"] == "closing"
    assert logger.fetch_position_events("pos-closing") == []
    with sqlite3.connect(db_path) as connection:
        status, closed_at = connection.execute(
            "select status, closed_at from managed_positions where position_id = ?",
            ("pos-closing",),
        ).fetchone()
    assert status == "closing"
    assert closed_at is None


def test_manage_positions_reconciles_multiple_distinct_spreads_for_same_symbol_and_expiry(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    for position_id, short_strike, long_strike in (
        ("pos-1", 160.0, 155.0),
        ("pos-2", 150.0, 145.0),
    ):
        logger.upsert_managed_position(
            {
                "position_id": position_id,
                "ticker": "AMD",
                "strategy": "bull_put_credit_spread",
                "expiry": "2026-04-30",
                "short_strike": short_strike,
                "long_strike": long_strike,
                "quantity": 1,
                "entry_credit": 1.05,
                "entry_order_id": 321,
                "mode": "paper",
                "status": "open",
                "opened_at": (
                    "2026-04-20T09:31:00+00:00"
                    if position_id == "pos-1"
                    else "2026-04-20T09:32:00+00:00"
                ),
                "closed_at": None,
                "last_known_debit": None,
                "last_evaluated_at": None,
                "broker_fingerprint": (
                    f"AMD|2026-04-30|P|{short_strike}|{long_strike}|1"
                ),
                "decision_reason": "opened by system",
                "risk_note": "",
            }
        )

    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=150.0,
                    broker_position_id="80150",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=145.0,
                    broker_position_id="80145",
                ),
            ],
            spread_debit=0.80,
            spot_price=180.0,
        ),
        executor=FakeManageExecutor(),
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = manager.manage_positions()

    positions = logger.fetch_active_managed_positions(mode="paper")
    assert result == {"status": "ok", "managed_count": 2}
    assert [position["position_id"] for position in positions] == ["pos-1", "pos-2"]
    assert positions[0]["last_known_debit"] == 0.80
    assert positions[1]["last_known_debit"] == 0.80


def test_manage_positions_finalizes_missing_closing_position_as_closed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "audit.db"
    logger = AuditLogger(db_path)
    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "closing",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": 0.42,
            "last_evaluated_at": "2026-04-20T10:00:00+00:00",
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "close in progress",
            "risk_note": "",
        }
    )
    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(option_positions=[]),
        executor=FakeManageExecutor(),
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = manager.manage_positions()

    assert result == {"status": "ok", "managed_count": 1}
    assert logger.fetch_active_managed_positions(mode="paper") == []
    assert logger.fetch_position_events("pos-1")[-1]["event_type"] == "closed"
    with sqlite3.connect(db_path) as connection:
        status, closed_at = connection.execute(
            "select status, closed_at from managed_positions where position_id = ?",
            ("pos-1",),
        ).fetchone()
    assert status == "closed"
    assert closed_at is not None


def test_manage_positions_does_not_submit_close_before_local_closing_state_persists(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "audit.db"
    base_logger = AuditLogger(db_path)
    base_logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "",
        }
    )
    market_data = FakeManageMarketData(
        option_positions=[
            BrokerOptionPosition(
                ticker="AMD",
                expiry="2026-04-30",
                right="P",
                quantity=-1,
                short_strike=160.0,
                broker_position_id="80160",
            ),
            BrokerOptionPosition(
                ticker="AMD",
                expiry="2026-04-30",
                right="P",
                quantity=1,
                short_strike=155.0,
                broker_position_id="80155",
            ),
        ],
        spread_debit=0.42,
        spot_price=171.0,
    )
    executor = FakeManageExecutor()
    failing_logger = FailingClosingUpdateAuditLogger(base_logger)
    manager = PositionManager(
        audit_logger=failing_logger,
        market_data=market_data,
        executor=executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    with pytest.raises(RuntimeError, match="closing update failed"):
        manager.manage_positions()

    persisted = base_logger.fetch_active_managed_positions(mode="paper")[0]
    assert persisted["status"] == "open"
    assert persisted["last_known_debit"] is None
    assert persisted["last_evaluated_at"] is None
    assert executor.calls == []

    second_executor = FakeManageExecutor()
    second_manager = PositionManager(
        audit_logger=base_logger,
        market_data=market_data,
        executor=second_executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = second_manager.manage_positions()

    assert result["status"] == "submitted"
    assert second_executor.calls == [("AMD", 0.42)]


def test_manage_positions_fails_closed_on_duplicate_identical_saved_strict_identities(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    for position_id in ("pos-1", "pos-2"):
        logger.upsert_managed_position(
            {
                "position_id": position_id,
                "ticker": "AMD",
                "strategy": "bull_put_credit_spread",
                "expiry": "2026-04-30",
                "short_strike": 160.0,
                "long_strike": 155.0,
                "quantity": 1,
                "entry_credit": 1.05,
                "entry_order_id": 321,
                "mode": "paper",
                "status": "open",
                "opened_at": (
                    "2026-04-20T09:31:00+00:00"
                    if position_id == "pos-1"
                    else "2026-04-20T09:32:00+00:00"
                ),
                "closed_at": None,
                "last_known_debit": None,
                "last_evaluated_at": None,
                "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
                "decision_reason": "opened by system",
                "risk_note": "",
            }
        )

    executor = FakeManageExecutor()
    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
            ],
            spread_debit=0.42,
            spot_price=171.0,
        ),
        executor=executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = manager.manage_positions()

    assert result == {
        "status": "anomaly",
        "reason": "unknown_broker_position",
        "fingerprints": ["AMD|2026-04-30|P|160.0|155.0|1"],
    }
    assert executor.calls == []
    positions = logger.fetch_active_managed_positions(mode="paper")
    assert positions[0]["status"] == "open"
    assert positions[1]["status"] == "open"


def test_manage_positions_returns_position_to_open_when_submit_fails_before_broker_submission(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "",
        }
    )
    market_data = FakeManageMarketData(
        option_positions=[
            BrokerOptionPosition(
                ticker="AMD",
                expiry="2026-04-30",
                right="P",
                quantity=-1,
                short_strike=160.0,
                broker_position_id="80160",
            ),
            BrokerOptionPosition(
                ticker="AMD",
                expiry="2026-04-30",
                right="P",
                quantity=1,
                short_strike=155.0,
                broker_position_id="80155",
            ),
        ],
        spread_debit=0.42,
        spot_price=171.0,
    )
    failing_executor = DeterministicPreSubmitManageExecutor()
    manager = PositionManager(
        audit_logger=logger,
        market_data=market_data,
        executor=failing_executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    with pytest.raises(OrderNotSubmittedError, match="order payload invalid"):
        manager.manage_positions()

    persisted = logger.fetch_active_managed_positions(mode="paper")[0]
    assert persisted["status"] == "open"
    assert persisted["last_known_debit"] == 0.42
    assert persisted["last_evaluated_at"] is not None
    assert failing_executor.calls == [("AMD", 0.42)]
    assert logger.fetch_position_events("pos-1") == []

    retry_executor = FakeManageExecutor()
    retry_manager = PositionManager(
        audit_logger=logger,
        market_data=market_data,
        executor=retry_executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = retry_manager.manage_positions()

    assert result["status"] == "submitted"
    assert retry_executor.calls == [("AMD", 0.42)]
    assert logger.fetch_active_managed_positions(mode="paper")[0]["status"] == "closing"


def test_manage_positions_leaves_position_closing_when_submit_fails_ambiguously(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "",
        }
    )
    market_data = FakeManageMarketData(
        option_positions=[
            BrokerOptionPosition(
                ticker="AMD",
                expiry="2026-04-30",
                right="P",
                quantity=-1,
                short_strike=160.0,
                broker_position_id="80160",
            ),
            BrokerOptionPosition(
                ticker="AMD",
                expiry="2026-04-30",
                right="P",
                quantity=1,
                short_strike=155.0,
                broker_position_id="80155",
            ),
        ],
        spread_debit=0.42,
        spot_price=171.0,
    )
    failing_executor = FailingSubmitManageExecutor()
    manager = PositionManager(
        audit_logger=logger,
        market_data=market_data,
        executor=failing_executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    with pytest.raises(RuntimeError, match="submit temporarily unavailable"):
        manager.manage_positions()

    persisted = logger.fetch_active_managed_positions(mode="paper")[0]
    assert persisted["status"] == "closing"
    assert persisted["last_known_debit"] == 0.42
    assert persisted["last_evaluated_at"] is not None
    assert failing_executor.calls == [("AMD", 0.42)]
    assert logger.fetch_position_events("pos-1")[-1]["event_type"] == "close_submit_uncertain"

    retry_executor = FakeManageExecutor()
    retry_manager = PositionManager(
        audit_logger=logger,
        market_data=market_data,
        executor=retry_executor,
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = retry_manager.manage_positions()

    assert result == {
        "status": "anomaly",
        "reason": "uncertain_submit_state",
        "fingerprints": ["AMD|2026-04-30|P|160.0|155.0|1"],
        "manual_intervention_required": True,
    }
    assert retry_executor.calls == []
    assert logger.fetch_active_managed_positions(mode="paper")[0]["status"] == "closing"


def test_manage_positions_treats_place_order_failure_as_ambiguous_submit(
    tmp_path: Path,
) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": None,
            "last_evaluated_at": None,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "opened by system",
            "risk_note": "",
        }
    )
    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
            ],
            spread_debit=0.42,
            spot_price=171.0,
        ),
        executor=IbkrExecutor(
            client=_FailingPlaceOrderIbClient(),
            ibkr_module=_FakeIbModule(),
        ),
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    with pytest.raises(RuntimeError, match="place order failed"):
        manager.manage_positions()

    persisted = logger.fetch_active_managed_positions(mode="paper")[0]
    assert persisted["status"] == "closing"
    assert persisted["last_known_debit"] == 0.42
    assert persisted["last_evaluated_at"] is not None
    assert logger.fetch_position_events("pos-1")[-1]["event_type"] == "close_submit_uncertain"

    retry_manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
            ],
            spread_debit=0.42,
            spot_price=171.0,
        ),
        executor=FakeManageExecutor(),
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = retry_manager.manage_positions()

    assert result == {
        "status": "anomaly",
        "reason": "uncertain_submit_state",
        "fingerprints": ["AMD|2026-04-30|P|160.0|155.0|1"],
        "manual_intervention_required": True,
    }


def test_manage_positions_finalizes_missing_closing_positions_before_returning_uncertain_submit_anomaly(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "audit.db"
    logger = AuditLogger(db_path)
    logger.upsert_managed_position(
        {
            "position_id": "pos-uncertain",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.05,
            "entry_order_id": 321,
            "mode": "paper",
            "status": "closing",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "closed_at": None,
            "last_known_debit": 0.42,
            "last_evaluated_at": "2026-04-20T10:00:00+00:00",
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "decision_reason": "close in progress",
            "risk_note": "",
        }
    )
    logger.record_position_event(
        "pos-uncertain",
        "close_submit_uncertain",
        {
            "exit_reason": "take_profit",
            "limit_price": 0.42,
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
            "error": "submit temporarily unavailable",
        },
    )
    logger.upsert_managed_position(
        {
            "position_id": "pos-missing",
            "ticker": "NVDA",
            "strategy": "bear_call_credit_spread",
            "expiry": "2026-05-15",
            "short_strike": 125.0,
            "long_strike": 130.0,
            "quantity": 1,
            "entry_credit": 1.10,
            "entry_order_id": 322,
            "mode": "paper",
            "status": "closing",
            "opened_at": "2026-04-20T09:32:00+00:00",
            "closed_at": None,
            "last_known_debit": 0.55,
            "last_evaluated_at": "2026-04-20T10:05:00+00:00",
            "broker_fingerprint": "NVDA|2026-05-15|C|125.0|130.0|1",
            "decision_reason": "close in progress",
            "risk_note": "",
        }
    )
    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=-1,
                    short_strike=160.0,
                    broker_position_id="80160",
                ),
                BrokerOptionPosition(
                    ticker="AMD",
                    expiry="2026-04-30",
                    right="P",
                    quantity=1,
                    short_strike=155.0,
                    broker_position_id="80155",
                ),
            ],
            spread_debit=0.42,
            spot_price=171.0,
        ),
        executor=FakeManageExecutor(),
        earnings_calendar=EarningsCalendar([]),
        risk_settings=SimpleNamespace(
            profit_take_pct=0.5,
            stop_loss_multiple=2.0,
            exit_dte_threshold=5,
        ),
        mode="paper",
        as_of=date(2026, 4, 20),
    )

    result = manager.manage_positions()

    assert result == {
        "status": "anomaly",
        "reason": "uncertain_submit_state",
        "fingerprints": ["AMD|2026-04-30|P|160.0|155.0|1"],
        "manual_intervention_required": True,
    }
    active_positions = logger.fetch_active_managed_positions(mode="paper")
    assert [position["position_id"] for position in active_positions] == ["pos-uncertain"]
    assert active_positions[0]["status"] == "closing"
    assert logger.fetch_position_events("pos-missing")[-1]["event_type"] == "closed"
    with sqlite3.connect(db_path) as connection:
        status, closed_at = connection.execute(
            "select status, closed_at from managed_positions where position_id = ?",
            ("pos-missing",),
        ).fetchone()
    assert status == "closed"
    assert closed_at is not None


class _FakeOption:
    def __init__(
        self,
        *,
        symbol: str,
        lastTradeDateOrContractMonth: str,
        strike: float,
        right: str,
        exchange: str,
        currency: str,
    ) -> None:
        self.symbol = symbol
        self.lastTradeDateOrContractMonth = lastTradeDateOrContractMonth
        self.strike = strike
        self.right = right
        self.exchange = exchange
        self.currency = currency
        self.conId = 0


class _FakeComboLeg:
    def __init__(self, *, conId: int, ratio: int, action: str, exchange: str) -> None:
        self.conId = conId
        self.ratio = ratio
        self.action = action
        self.exchange = exchange


class _FakeContract:
    def __init__(
        self,
        *,
        symbol: str,
        secType: str,
        currency: str,
        exchange: str,
        comboLegs: list[_FakeComboLeg],
    ) -> None:
        self.symbol = symbol
        self.secType = secType
        self.currency = currency
        self.exchange = exchange
        self.comboLegs = comboLegs


class _FakeLimitOrder:
    def __init__(
        self,
        action: str,
        totalQuantity: int,
        lmtPrice: float,
        *,
        transmit: bool,
    ) -> None:
        self.action = action
        self.totalQuantity = totalQuantity
        self.lmtPrice = lmtPrice
        self.transmit = transmit
        self.orderType = "LMT"
        self.orderId = 404


class _FakeIbModule:
    Option = _FakeOption
    ComboLeg = _FakeComboLeg
    Contract = _FakeContract
    LimitOrder = _FakeLimitOrder


class _FakeIbClient:
    def qualifyContracts(self, *contracts: _FakeOption) -> list[_FakeOption]:
        for contract in contracts:
            contract.conId = 50000 + int(contract.strike)
        return list(contracts)

    def placeOrder(self, contract: _FakeContract, order: _FakeLimitOrder):
        return type(
            "Trade",
            (),
            {
                "contract": contract,
                "order": order,
                "orderStatus": type("OrderStatus", (), {"status": "PendingSubmit"})(),
            },
        )()


class _FailingQualificationIbClient(_FakeIbClient):
    def qualifyContracts(self, *contracts: _FakeOption) -> list[_FakeOption]:
        return list(contracts[:-1])


class _FailingPlaceOrderIbClient(_FakeIbClient):
    def placeOrder(self, contract: _FakeContract, order: _FakeLimitOrder):
        raise RuntimeError("place order failed")


class FakeManageMarketData:
    def __init__(
        self,
        *,
        option_positions: list[BrokerOptionPosition],
        spread_debit: float = 0.85,
        spot_price: float = 150.0,
    ) -> None:
        self._option_positions = option_positions
        self._spread_debit = spread_debit
        self._spot_price = spot_price

    def fetch_option_positions(self) -> list[BrokerOptionPosition]:
        return list(self._option_positions)

    def estimate_spread_debit(
        self,
        *,
        ticker: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        strategy: str,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> float:
        return self._spread_debit

    def fetch_spot_price(
        self,
        ticker: str,
        *,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> float:
        return self._spot_price


class FailingManageMarketData(FakeManageMarketData):
    def __init__(
        self,
        *,
        option_positions: list[BrokerOptionPosition],
        raise_on_ticker: str,
        spread_debit: float = 0.85,
        spot_price: float = 150.0,
    ) -> None:
        super().__init__(
            option_positions=option_positions,
            spread_debit=spread_debit,
            spot_price=spot_price,
        )
        self._raise_on_ticker = raise_on_ticker

    def estimate_spread_debit(
        self,
        *,
        ticker: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        strategy: str,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> float:
        if ticker == self._raise_on_ticker:
            raise RuntimeError(f"debit unavailable for {ticker}")
        return super().estimate_spread_debit(
            ticker=ticker,
            expiry=expiry,
            short_strike=short_strike,
            long_strike=long_strike,
            strategy=strategy,
            exchange=exchange,
            currency=currency,
        )


class FakeManageExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []
        self.positions: list[PositionSnapshot] = []

    def submit_limit_combo(
        self,
        position: PositionSnapshot,
        *,
        limit_price: float,
    ) -> dict[str, object]:
        self.calls.append((position.ticker, limit_price))
        self.positions.append(position)
        return {
            "status": "submitted",
            "order_id": 999,
            "limit_price": limit_price,
            "broker_fingerprint": (
                f"{position.ticker}|{position.expiry}|P|"
                f"{float(position.short_strike)}|{float(position.long_strike)}|"
                f"{int(position.quantity)}"
            ),
        }


class FailingSubmitManageExecutor(FakeManageExecutor):
    def submit_limit_combo(
        self,
        position: PositionSnapshot,
        *,
        limit_price: float,
    ) -> dict[str, object]:
        self.calls.append((position.ticker, limit_price))
        self.positions.append(position)
        raise RuntimeError("submit temporarily unavailable")


class DeterministicPreSubmitManageExecutor(FakeManageExecutor):
    def submit_limit_combo(
        self,
        position: PositionSnapshot,
        *,
        limit_price: float,
    ) -> dict[str, object]:
        self.calls.append((position.ticker, limit_price))
        self.positions.append(position)
        raise OrderNotSubmittedError("order payload invalid")


class FailingClosingUpdateAuditLogger:
    def __init__(self, base_logger: AuditLogger) -> None:
        self._base_logger = base_logger

    def fetch_active_managed_positions(self, *, mode: str) -> list[dict[str, object]]:
        return self._base_logger.fetch_active_managed_positions(mode=mode)

    def upsert_managed_position(self, payload: dict[str, object]) -> None:
        self._base_logger.upsert_managed_position(payload)

    def update_managed_position_if_status(
        self,
        position_id: str,
        *,
        expected_status: str,
        **updates: object,
    ) -> bool:
        if expected_status == "open" and updates.get("status") == "closing":
            raise RuntimeError("closing update failed")
        return self._base_logger.update_managed_position_if_status(
            position_id,
            expected_status=expected_status,
            **updates,
        )

    def update_managed_position(self, position_id: str, **updates: object) -> None:
        if updates.get("status") == "closing":
            raise RuntimeError("closing update failed")
        self._base_logger.update_managed_position(position_id, **updates)

    def record_position_event(
        self,
        position_id: str,
        event_type: str,
        payload: dict[str, object],
        *,
        created_at: datetime | None = None,
    ) -> None:
        self._base_logger.record_position_event(
            position_id,
            event_type,
            payload,
            created_at=created_at,
        )
