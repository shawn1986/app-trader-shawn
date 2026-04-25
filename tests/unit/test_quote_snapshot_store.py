from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from trader_shawn.domain.models import OptionQuote
from trader_shawn.monitoring.quote_snapshot_store import QuoteSnapshotStore


def test_quote_snapshot_store_records_snapshot_and_option_quotes(tmp_path) -> None:
    store = QuoteSnapshotStore(tmp_path / "audit.db")
    collected_at = datetime(2026, 4, 25, 1, 2, 3, tzinfo=UTC)

    snapshot_id = store.record_symbol_quotes(
        "AMD",
        [
            OptionQuote(
                symbol="AMD",
                expiry="2026-05-08",
                strike=160,
                right="P",
                bid=1.1,
                ask=1.3,
                delta=-0.21,
                last=1.2,
                mark=1.2,
                volume=140,
                open_interest=610,
            )
        ],
        market_data_type="delayed",
        scan_inputs={"min_dte": 5, "max_dte": 35},
        collected_at=collected_at,
    )

    with sqlite3.connect(tmp_path / "audit.db") as connection:
        snapshot = connection.execute(
            "select symbol, collected_at, market_data_type, quote_count, scan_inputs_json "
            "from quote_snapshots where id = ?",
            (snapshot_id,),
        ).fetchone()
        quote = connection.execute(
            "select symbol, expiry, strike, right, bid, ask, delta, last, mark, volume, open_interest "
            "from option_quotes where snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()

    assert snapshot == (
        "AMD",
        "2026-04-25T01:02:03+00:00",
        "delayed",
        1,
        '{"max_dte": 35, "min_dte": 5}',
    )
    assert quote == (
        "AMD",
        "2026-05-08",
        160.0,
        "P",
        1.1,
        1.3,
        -0.21,
        1.2,
        1.2,
        140,
        610,
    )
