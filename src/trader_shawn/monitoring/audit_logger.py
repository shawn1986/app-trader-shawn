from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from trader_shawn.domain.models import DecisionRecord


class AuditLogger:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """
                create table if not exists decisions (
                    id integer primary key autoincrement,
                    cycle_id text not null,
                    provider text not null,
                    action text not null,
                    ticker text not null,
                    payload text not null,
                    created_at text not null
                )
                """
            )
            connection.commit()

    def record_decision(self, record: DecisionRecord) -> None:
        row = record.to_row()
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """
                insert into decisions (
                    cycle_id,
                    provider,
                    action,
                    ticker,
                    payload,
                    created_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["cycle_id"],
                    row["provider"],
                    row["action"],
                    row["ticker"],
                    json.dumps(row["payload"], sort_keys=True),
                    row["created_at"],
                ),
            )
            connection.commit()
