from pathlib import Path
import sqlite3

from trader_shawn.domain.models import DecisionRecord
from trader_shawn.monitoring.audit_logger import AuditLogger


def test_audit_logger_persists_decision_record(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    logger = AuditLogger(db_path)

    logger.record_decision(
        DecisionRecord(
            cycle_id="cycle-1",
            provider="claude_cli",
            action="approve",
            ticker="AMD",
            payload={"confidence": 0.71},
        )
    )

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "select cycle_id, provider, action, ticker from decisions"
        ).fetchone()

    assert row == ("cycle-1", "claude_cli", "approve", "AMD")
