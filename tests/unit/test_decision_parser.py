from pathlib import Path
from datetime import UTC, datetime
import json
import sqlite3

import pytest

from trader_shawn.domain.enums import DecisionAction
from trader_shawn.domain.models import DecisionRecord
from trader_shawn.monitoring.audit_logger import AuditLogger
from trader_shawn.monitoring.state_store import StateStore


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


def test_state_store_save_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state" / "runtime.json"
    store = StateStore(path)
    state = {"cycle_id": "cycle-1", "tickers": ["AMD", "NVDA"]}

    store.save(state)

    assert store.load() == state
    assert not list(path.parent.glob("*.tmp"))


def test_state_store_returns_empty_state_for_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")
    store = StateStore(path)

    assert store.load() == {}


@pytest.mark.parametrize("raw_value", ["[]", '"x"', "1"])
def test_state_store_returns_empty_state_for_non_object_json(
    tmp_path: Path,
    raw_value: str,
) -> None:
    path = tmp_path / "state.json"
    path.write_text(raw_value, encoding="utf-8")
    store = StateStore(path)

    assert store.load() == {}
