from datetime import UTC, datetime
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

import pytest

from trader_shawn.domain.enums import DecisionAction, PositionSide
from trader_shawn.domain.models import DecisionRecord, PositionSnapshot
from trader_shawn.monitoring.audit_logger import AuditLogger
from trader_shawn.monitoring.state_store import StateStore


@dataclass(slots=True)
class PayloadSnapshot:
    ticker: str
    reviewed_at: datetime
    action: DecisionAction


def test_audit_logger_persists_decision_record(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    logger = AuditLogger(db_path)
    created_at = datetime(2026, 4, 20, 9, 30, tzinfo=UTC)

    logger.record_decision(
        DecisionRecord(
            cycle_id="cycle-1",
            provider="claude_cli",
            action="approve",
            ticker="AMD",
            payload={"confidence": 0.71},
            created_at=created_at,
        )
    )

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "select cycle_id, provider, action, ticker, payload, created_at from decisions"
        ).fetchone()

    assert row == (
        "cycle-1",
        "claude_cli",
        "approve",
        "AMD",
        json.dumps({"confidence": 0.71}, sort_keys=True),
        created_at.isoformat(),
    )


def test_audit_logger_serializes_datetime_enum_and_dataclass_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    logger = AuditLogger(db_path)
    reviewed_at = datetime(2026, 4, 20, 9, 45, tzinfo=UTC)

    logger.record_decision(
        DecisionRecord(
            cycle_id="cycle-2",
            provider="claude_cli",
            action="approve",
            ticker="AMD",
            payload={
                "reviewed_at": reviewed_at,
                "decision": DecisionAction.APPROVE,
                "snapshot": PayloadSnapshot(
                    ticker="AMD",
                    reviewed_at=reviewed_at,
                    action=DecisionAction.HOLD,
                ),
            },
        )
    )

    with sqlite3.connect(db_path) as connection:
        payload = connection.execute("select payload from decisions").fetchone()[0]

    assert json.loads(payload) == {
        "decision": "approve",
        "reviewed_at": reviewed_at.isoformat(),
        "snapshot": {
            "action": "hold",
            "reviewed_at": reviewed_at.isoformat(),
            "ticker": "AMD",
        },
    }


def test_audit_logger_rejects_unsupported_payload_values_with_controlled_error(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "audit.db"
    logger = AuditLogger(db_path)

    with pytest.raises(TypeError, match="unsupported decision payload"):
        logger.record_decision(
            DecisionRecord(
                cycle_id="cycle-3",
                provider="claude_cli",
                action="approve",
                ticker="AMD",
                payload={"unsupported": object()},
            )
        )


def test_decision_record_converts_valid_string_action_to_enum() -> None:
    record = DecisionRecord(
        cycle_id="cycle-1",
        provider="claude_cli",
        action="approve",
        ticker="AMD",
    )

    assert record.action is DecisionAction.APPROVE


def test_decision_record_rejects_invalid_action() -> None:
    with pytest.raises(ValueError, match="invalid decision action"):
        DecisionRecord(
            cycle_id="cycle-1",
            provider="claude_cli",
            action="ship-it",
            ticker="AMD",
        )


def test_position_snapshot_converts_valid_string_side_to_enum() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        quantity=1,
        average_cost=1.25,
        market_value=1.10,
        unrealized_pnl=-15,
        side="short",
    )

    assert position.side is PositionSide.SHORT


def test_position_snapshot_rejects_invalid_side() -> None:
    with pytest.raises(ValueError, match="invalid position side"):
        PositionSnapshot(
            ticker="AMD",
            quantity=1,
            average_cost=1.25,
            market_value=1.10,
            unrealized_pnl=-15,
            side="flat",
        )


def test_state_store_save_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state" / "runtime.json"
    store = StateStore(path)
    state = {"cycle_id": "cycle-1", "tickers": ["AMD", "NVDA"]}

    store.save(state)

    assert store.load() == state
    assert not list(path.parent.glob("*.tmp"))


def test_state_store_raises_for_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")
    store = StateStore(path)

    with pytest.raises(ValueError, match="invalid state file"):
        store.load()


@pytest.mark.parametrize("raw_value", ["[]", '"x"', "1"])
def test_state_store_raises_for_non_object_json(
    tmp_path: Path,
    raw_value: str,
) -> None:
    path = tmp_path / "state.json"
    path.write_text(raw_value, encoding="utf-8")
    store = StateStore(path)

    with pytest.raises(ValueError, match="state file must contain a JSON object"):
        store.load()
