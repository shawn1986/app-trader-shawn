from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ContextManager

from trader_shawn.domain.models import (
    DecisionRecord,
    ManagedPositionRecord,
    _json_safe_payload,
)


class AuditLogger:
    _MANAGED_POSITION_COLUMNS = (
        "position_id",
        "ticker",
        "strategy",
        "expiry",
        "short_strike",
        "long_strike",
        "quantity",
        "entry_credit",
        "entry_order_id",
        "mode",
        "status",
        "opened_at",
        "closed_at",
        "last_known_debit",
        "last_evaluated_at",
        "broker_fingerprint",
        "decision_reason",
        "risk_note",
    )

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> ContextManager[sqlite3.Connection]:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        return closing(connection)

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
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
            connection.execute(
                """
                create table if not exists managed_positions (
                    position_id text primary key,
                    ticker text not null,
                    strategy text not null,
                    expiry text not null,
                    short_strike real not null,
                    long_strike real not null,
                    quantity integer not null,
                    entry_credit real not null,
                    entry_order_id integer,
                    mode text not null,
                    status text not null,
                    opened_at text not null,
                    closed_at text,
                    last_known_debit real,
                    last_evaluated_at text,
                    broker_fingerprint text not null,
                    decision_reason text,
                    risk_note text
                )
                """
            )
            connection.execute(
                """
                create table if not exists position_events (
                    id integer primary key autoincrement,
                    position_id text not null,
                    event_type text not null,
                    payload_json text not null,
                    created_at text not null,
                    foreign key (position_id) references managed_positions(position_id)
                )
                """
            )
            self._migrate_position_events_payload_column(connection)
            connection.commit()

    def _migrate_position_events_payload_column(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute("pragma table_info(position_events)").fetchall()
        }
        if "payload_json" in columns or "payload" not in columns:
            return
        connection.execute(
            """
            alter table position_events
            rename column payload to payload_json
            """
        )

    def _serialize_json(self, payload: Any, *, message: str) -> str:
        try:
            normalized = _json_safe_payload(payload)
            return json.dumps(normalized, sort_keys=True)
        except TypeError as exc:
            raise TypeError(message) from exc

    def _coerce_managed_position_record(
        self,
        record: ManagedPositionRecord | dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(record, ManagedPositionRecord):
            row = record.to_row()
        else:
            row = ManagedPositionRecord(**record).to_row()
        return row

    def record_decision(self, record: DecisionRecord) -> None:
        row = record.to_row()
        payload = self._serialize_json(
            row["payload"],
            message="unsupported decision payload",
        )
        with self._connect() as connection:
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
                    payload,
                    row["created_at"],
                ),
            )
            connection.commit()

    def upsert_managed_position(
        self,
        record: ManagedPositionRecord | dict[str, Any],
    ) -> None:
        row = self._coerce_managed_position_record(record)
        columns = ", ".join(self._MANAGED_POSITION_COLUMNS)
        placeholders = ", ".join("?" for _ in self._MANAGED_POSITION_COLUMNS)
        update_columns = ", ".join(
            f"{column}=excluded.{column}"
            for column in self._MANAGED_POSITION_COLUMNS
            if column != "position_id"
        )
        values = tuple(row[column] for column in self._MANAGED_POSITION_COLUMNS)
        with self._connect() as connection:
            connection.execute(
                f"""
                insert into managed_positions ({columns})
                values ({placeholders})
                on conflict(position_id) do update set {update_columns}
                """,
                values,
            )
            connection.commit()

    def update_managed_position(self, position_id: str, **updates: Any) -> None:
        if not updates:
            return
        normalized_updates = self._normalize_managed_position_updates(updates)
        assignments = ", ".join(f"{column} = ?" for column in normalized_updates)
        values = tuple(normalized_updates.values()) + (position_id,)
        with self._connect() as connection:
            connection.execute(
                f"""
                update managed_positions
                set {assignments}
                where position_id = ?
                """,
                values,
            )
            connection.commit()

    def update_managed_position_if_status(
        self,
        position_id: str,
        *,
        expected_status: str,
        **updates: Any,
    ) -> bool:
        if not updates:
            return False
        normalized_updates = self._normalize_managed_position_updates(updates)
        assignments = ", ".join(f"{column} = ?" for column in normalized_updates)
        values = tuple(normalized_updates.values()) + (position_id, expected_status)
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                update managed_positions
                set {assignments}
                where position_id = ? and status = ?
                """,
                values,
            )
            connection.commit()
        return cursor.rowcount == 1

    def _normalize_managed_position_updates(
        self,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        invalid_columns = sorted(
            set(updates) - (set(self._MANAGED_POSITION_COLUMNS) - {"position_id"})
        )
        if invalid_columns:
            names = ", ".join(invalid_columns)
            raise ValueError(f"unsupported managed position fields: {names}")
        return {
            key: _json_safe_payload(value, path=key) for key, value in updates.items()
        }

    def fetch_active_managed_positions(self, *, mode: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                select {", ".join(self._MANAGED_POSITION_COLUMNS)}
                from managed_positions
                where mode = ? and status in ('open', 'closing')
                order by opened_at asc, position_id asc
                """,
                (mode,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_position_event(
        self,
        position_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        created_at: datetime | None = None,
    ) -> None:
        serialized_payload = self._serialize_json(
            payload,
            message="unsupported position event payload",
        )
        event_created_at = (created_at or datetime.now(UTC)).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                insert into position_events (
                    position_id,
                    event_type,
                    payload_json,
                    created_at
                ) values (?, ?, ?, ?)
                """,
                (position_id, event_type, serialized_payload, event_created_at),
            )
            connection.commit()

    def fetch_position_events(self, position_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select
                    id,
                    position_id,
                    event_type,
                    payload_json,
                    created_at
                from position_events
                where position_id = ?
                order by id asc
                """,
                (position_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "position_id": row["position_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
