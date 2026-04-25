from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ContextManager

from trader_shawn.domain.models import OptionQuote, _json_safe_payload


class QuoteSnapshotStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._schema_ready = False

    def _connect(self) -> ContextManager[sqlite3.Connection]:
        self._ensure_schema()
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        return closing(connection)

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._db_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("pragma foreign_keys = on")
            connection.execute(
                """
                create table if not exists quote_snapshots (
                    id integer primary key autoincrement,
                    symbol text not null,
                    collected_at text not null,
                    market_data_type text not null,
                    quote_count integer not null,
                    scan_inputs_json text not null
                )
                """
            )
            connection.execute(
                """
                create table if not exists option_quotes (
                    id integer primary key autoincrement,
                    snapshot_id integer not null,
                    symbol text not null,
                    expiry text not null,
                    strike real not null,
                    right text not null,
                    bid real not null,
                    ask real not null,
                    delta real,
                    last real,
                    mark real,
                    volume integer not null,
                    open_interest integer not null,
                    foreign key (snapshot_id) references quote_snapshots(id)
                )
                """
            )
            connection.execute(
                """
                create index if not exists idx_quote_snapshots_symbol_time
                on quote_snapshots(symbol, collected_at)
                """
            )
            connection.execute(
                """
                create index if not exists idx_option_quotes_snapshot
                on option_quotes(snapshot_id)
                """
            )
            connection.commit()
        self._schema_ready = True

    def record_symbol_quotes(
        self,
        symbol: str,
        quotes: list[OptionQuote],
        *,
        market_data_type: str,
        scan_inputs: dict[str, Any] | None = None,
        collected_at: datetime | None = None,
    ) -> int:
        timestamp = collected_at or datetime.now(UTC)
        scan_inputs_json = json.dumps(
            _json_safe_payload(scan_inputs or {}),
            sort_keys=True,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                insert into quote_snapshots (
                    symbol,
                    collected_at,
                    market_data_type,
                    quote_count,
                    scan_inputs_json
                ) values (?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    timestamp.isoformat(),
                    market_data_type,
                    len(quotes),
                    scan_inputs_json,
                ),
            )
            snapshot_id = int(cursor.lastrowid)
            connection.executemany(
                """
                insert into option_quotes (
                    snapshot_id,
                    symbol,
                    expiry,
                    strike,
                    right,
                    bid,
                    ask,
                    delta,
                    last,
                    mark,
                    volume,
                    open_interest
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        quote.symbol,
                        quote.expiry,
                        quote.strike,
                        quote.right,
                        quote.bid,
                        quote.ask,
                        quote.delta,
                        quote.last,
                        quote.mark,
                        quote.volume,
                        quote.open_interest,
                    )
                    for quote in quotes
                ],
            )
            connection.commit()
        return snapshot_id
