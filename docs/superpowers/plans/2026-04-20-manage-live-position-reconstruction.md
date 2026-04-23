# Manage Live Position Reconstruction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real `manage` runtime that reconstructs only system-owned credit spreads from SQLite state plus IBKR verification, evaluates exits, submits close combo orders, and fails closed on unknown broker option positions.

**Architecture:** Keep SQLite as the durable source of truth for managed spreads and lifecycle events, with JSON state still limited to dashboard snapshots. Extend IBKR transport with just enough live-position and spread-pricing support, then layer a strict reconciliation workflow in `positions.manager` and wire `app.py` to call it for the `manage` command.

**Tech Stack:** Python 3.12, `sqlite3`, `ib_insync`, `click`, `pytest`

---

## File Structure

Create or modify these files during implementation:

- `D:\Codes\trader-shawn\src\trader_shawn\domain\models.py`
- `D:\Codes\trader-shawn\src\trader_shawn\monitoring\audit_logger.py`
- `D:\Codes\trader-shawn\src\trader_shawn\monitoring\dashboard_api.py`
- `D:\Codes\trader-shawn\src\trader_shawn\market_data\ibkr_market_data.py`
- `D:\Codes\trader-shawn\src\trader_shawn\execution\ibkr_executor.py`
- `D:\Codes\trader-shawn\src\trader_shawn\positions\manager.py`
- `D:\Codes\trader-shawn\src\trader_shawn\app.py`
- `D:\Codes\trader-shawn\tests\unit\test_position_manager.py`
- `D:\Codes\trader-shawn\tests\unit\test_ibkr_transport.py`
- `D:\Codes\trader-shawn\tests\integration\test_cli_commands.py`

Responsibility map:

- `domain/models.py`: durable record types for managed positions and broker option legs
- `monitoring/audit_logger.py`: SQLite schema + CRUD for `managed_positions` and `position_events`
- `market_data/ibkr_market_data.py`: list live option positions and estimate current spread debit
- `execution/ibkr_executor.py`: close-order submission helper that returns broker metadata
- `positions/manager.py`: reconciliation, exit evaluation, anomaly handling, and lifecycle writes
- `app.py`: build the real position manager into runtime and expose it through `manage`

### Task 1: Add SQLite Managed Position Persistence

**Files:**
- Modify: `D:\Codes\trader-shawn\src\trader_shawn\domain\models.py`
- Modify: `D:\Codes\trader-shawn\src\trader_shawn\monitoring\audit_logger.py`
- Test: `D:\Codes\trader-shawn\tests\unit\test_position_manager.py`

- [ ] **Step 1: Write the failing persistence test**

Add these tests to `D:\Codes\trader-shawn\tests\unit\test_position_manager.py`:

```python
from pathlib import Path

from trader_shawn.monitoring.audit_logger import AuditLogger


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
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
py -3.12 -m pytest D:\Codes\trader-shawn\tests\unit\test_position_manager.py -k "managed_position" -q
```

Expected: FAIL with `AttributeError` because `AuditLogger` does not expose the managed-position methods yet.

- [ ] **Step 3: Implement the minimal persistence layer**

Modify `D:\Codes\trader-shawn\src\trader_shawn\domain\models.py` to add durable records:

```python
@dataclass(slots=True)
class ManagedPositionRecord:
    position_id: str
    ticker: str
    strategy: str
    expiry: str
    short_strike: float
    long_strike: float
    quantity: int
    entry_credit: float
    entry_order_id: int | None
    mode: str
    status: str
    opened_at: str
    closed_at: str | None = None
    last_known_debit: float | None = None
    last_evaluated_at: str | None = None
    broker_fingerprint: str = ""
    decision_reason: str = ""
    risk_note: str = ""


@dataclass(slots=True)
class BrokerOptionPosition:
    symbol: str
    expiry: str
    right: str
    strike: float
    quantity: int
    market_price: float | None = None
    average_cost: float | None = None
    con_id: int | None = None
```

Modify `D:\Codes\trader-shawn\src\trader_shawn\monitoring\audit_logger.py` to extend the schema and add CRUD helpers:

```python
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
                    decision_reason text not null,
                    risk_note text not null
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
                    created_at text not null
                )
                """
            )
```

Add these methods:

```python
    def upsert_managed_position(self, record: dict[str, object]) -> None:
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """
                insert into managed_positions (
                    position_id, ticker, strategy, expiry, short_strike, long_strike,
                    quantity, entry_credit, entry_order_id, mode, status,
                    opened_at, closed_at, last_known_debit, last_evaluated_at,
                    broker_fingerprint, decision_reason, risk_note
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(position_id) do update set
                    status=excluded.status,
                    closed_at=excluded.closed_at,
                    last_known_debit=excluded.last_known_debit,
                    last_evaluated_at=excluded.last_evaluated_at,
                    broker_fingerprint=excluded.broker_fingerprint,
                    decision_reason=excluded.decision_reason,
                    risk_note=excluded.risk_note
                """,
                (
                    record["position_id"],
                    record["ticker"],
                    record["strategy"],
                    record["expiry"],
                    record["short_strike"],
                    record["long_strike"],
                    record["quantity"],
                    record["entry_credit"],
                    record["entry_order_id"],
                    record["mode"],
                    record["status"],
                    record["opened_at"],
                    record["closed_at"],
                    record["last_known_debit"],
                    record["last_evaluated_at"],
                    record["broker_fingerprint"],
                    record["decision_reason"],
                    record["risk_note"],
                ),
            )
            connection.commit()

    def update_managed_position(self, position_id: str, **updates: object) -> None:
        assignments = ", ".join(f"{column}=?" for column in updates)
        values = list(updates.values()) + [position_id]
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                f"update managed_positions set {assignments} where position_id=?",
                values,
            )
            connection.commit()

    def fetch_active_managed_positions(self, *, mode: str) -> list[dict[str, object]]:
        with sqlite3.connect(self._db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select * from managed_positions
                where mode = ? and status in ('open', 'closing')
                order by opened_at asc
                """,
                (mode,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_position_event(
        self,
        position_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """
                insert into position_events (position_id, event_type, payload_json, created_at)
                values (?, ?, ?, ?)
                """,
                (
                    position_id,
                    event_type,
                    json.dumps(payload, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.commit()

    def fetch_position_events(self, position_id: str) -> list[dict[str, object]]:
        with sqlite3.connect(self._db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select position_id, event_type, payload_json, created_at
                from position_events
                where position_id = ?
                order by id asc
                """,
                (position_id,),
            ).fetchall()
        return [
            {
                "position_id": row["position_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```powershell
py -3.12 -m pytest D:\Codes\trader-shawn\tests\unit\test_position_manager.py -k "managed_position" -q
```

Expected: PASS with `1 passed`.

- [ ] **Step 5: Commit**

Run:

```powershell
git add D:\Codes\trader-shawn\src\trader_shawn\domain\models.py D:\Codes\trader-shawn\src\trader_shawn\monitoring\audit_logger.py D:\Codes\trader-shawn\tests\unit\test_position_manager.py
git commit -m "feat: persist managed position lifecycle"
```

### Task 2: Extend IBKR Transport For Live Position Reconstruction

**Files:**
- Modify: `D:\Codes\trader-shawn\src\trader_shawn\market_data\ibkr_market_data.py`
- Modify: `D:\Codes\trader-shawn\src\trader_shawn\execution\ibkr_executor.py`
- Test: `D:\Codes\trader-shawn\tests\unit\test_ibkr_transport.py`

- [ ] **Step 1: Write the failing transport tests**

Add these tests to `D:\Codes\trader-shawn\tests\unit\test_ibkr_transport.py`:

```python
def test_ibkr_market_data_client_lists_live_option_positions_for_manage() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(client=ib_client, ibkr_module=FakeIbModule())

    positions = client.fetch_option_positions()

    assert positions == [
        BrokerOptionPosition(
            symbol="AMD",
            expiry="2026-04-30",
            right="P",
            strike=160.0,
            quantity=-1,
            market_price=1.35,
            average_cost=1.05,
            con_id=80160,
        ),
        BrokerOptionPosition(
            symbol="AMD",
            expiry="2026-04-30",
            right="P",
            strike=155.0,
            quantity=1,
            market_price=0.72,
            average_cost=0.33,
            con_id=80155,
        ),
    ]


def test_ibkr_market_data_client_estimates_spread_debit_from_live_legs() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(client=ib_client, ibkr_module=FakeIbModule())

    debit = client.estimate_spread_debit(
        ticker="AMD",
        expiry="2026-04-30",
        short_strike=160.0,
        long_strike=155.0,
        strategy="bull_put_credit_spread",
    )

    assert debit == 0.63


def test_ibkr_executor_submit_limit_combo_returns_broker_fingerprint() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=1.00,
        current_debit=0.63,
        dte=9,
        short_leg_distance_pct=0.08,
        quantity=1,
    )
    executor = IbkrExecutor(client=FakeExecutionClient(), ibkr_module=FakeIbModule())

    result = executor.submit_limit_combo(position, limit_price=0.63)

    assert result["broker_fingerprint"] == "AMD|2026-04-30|P|160.0|155.0|1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
py -3.12 -m pytest D:\Codes\trader-shawn\tests\unit\test_ibkr_transport.py -k "live_option_positions or spread_debit or broker_fingerprint" -q
```

Expected: FAIL because the transport methods and metadata do not exist yet.

- [ ] **Step 3: Implement the live transport helpers**

Modify `D:\Codes\trader-shawn\src\trader_shawn\market_data\ibkr_market_data.py` to add broker-position extraction:

```python
    def fetch_option_positions(self) -> list[BrokerOptionPosition]:
        positions: list[BrokerOptionPosition] = []
        for row in self.ensure_connected().positions():
            contract = getattr(row, "contract", None)
            if contract is None:
                continue
            if str(getattr(contract, "secType", "")).upper() not in {"OPT", "FOP"}:
                continue
            positions.append(
                BrokerOptionPosition(
                    symbol=str(getattr(contract, "symbol", "")),
                    expiry=_normalize_expiry(
                        str(getattr(contract, "lastTradeDateOrContractMonth", ""))
                    ),
                    right=str(getattr(contract, "right", "")).upper(),
                    strike=float(getattr(contract, "strike", 0.0)),
                    quantity=int(float(getattr(row, "position", 0))),
                    market_price=_optional_float(getattr(row, "marketPrice", None)),
                    average_cost=_optional_float(getattr(row, "avgCost", None)),
                    con_id=_optional_int(getattr(contract, "conId", None)),
                )
            )
        return sorted(
            positions,
            key=lambda item: (item.symbol, item.expiry, item.right, item.strike, item.quantity),
        )

    def estimate_spread_debit(
        self,
        *,
        ticker: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        strategy: str,
    ) -> float:
        right = "P" if strategy == "bull_put_credit_spread" else "C"
        quotes = self.fetch_option_quotes(
            ticker,
            min_dte=0,
            max_dte=365,
            rights=(right,),
            as_of=date.today(),
        )
        short_quote = next(
            quote
            for quote in quotes
            if quote.expiry == expiry and math.isclose(quote.strike, short_strike)
        )
        long_quote = next(
            quote
            for quote in quotes
            if quote.expiry == expiry and math.isclose(quote.strike, long_strike)
        )
        short_mark = short_quote.mark if short_quote.mark is not None else short_quote.ask
        long_mark = long_quote.mark if long_quote.mark is not None else long_quote.bid
        return round(short_mark - long_mark, 10)
```

Modify `D:\Codes\trader-shawn\src\trader_shawn\execution\ibkr_executor.py` so the submission result includes a stable fingerprint:

```python
        return {
            "status": "submitted",
            "broker": "ibkr",
            "order_id": _extract_order_id(trade, order),
            "broker_fingerprint": _broker_fingerprint(
                symbol=symbol,
                expiry=str(leg_payloads[0]["expiry"]),
                right=str(leg_payloads[0]["right"]),
                short_strike=float(leg_payloads[0]["strike"]),
                long_strike=float(leg_payloads[1]["strike"]),
                quantity=int(order_payload["totalQuantity"]),
            ),
            ...
        }


def _broker_fingerprint(
    *,
    symbol: str,
    expiry: str,
    right: str,
    short_strike: float,
    long_strike: float,
    quantity: int,
) -> str:
    return f"{symbol}|{expiry}|{right}|{short_strike}|{long_strike}|{quantity}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```powershell
py -3.12 -m pytest D:\Codes\trader-shawn\tests\unit\test_ibkr_transport.py -k "live_option_positions or spread_debit or broker_fingerprint" -q
```

Expected: PASS with the three new tests green.

- [ ] **Step 5: Commit**

Run:

```powershell
git add D:\Codes\trader-shawn\src\trader_shawn\market_data\ibkr_market_data.py D:\Codes\trader-shawn\src\trader_shawn\execution\ibkr_executor.py D:\Codes\trader-shawn\tests\unit\test_ibkr_transport.py
git commit -m "feat: add live manage transport helpers"
```

### Task 3: Implement Strict Reconciliation And Exit Workflow

**Files:**
- Modify: `D:\Codes\trader-shawn\src\trader_shawn\positions\manager.py`
- Modify: `D:\Codes\trader-shawn\src\trader_shawn\monitoring\audit_logger.py`
- Test: `D:\Codes\trader-shawn\tests\unit\test_position_manager.py`

- [ ] **Step 1: Write the failing manage-workflow tests**

Add these tests to `D:\Codes\trader-shawn\tests\unit\test_position_manager.py`:

```python
def test_manage_positions_fails_closed_on_unknown_broker_option_position(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.db")
    manager = PositionManager(
        audit_logger=logger,
        market_data=FakeManageMarketData(
            option_positions=[
                BrokerOptionPosition(
                    symbol="AMD",
                    expiry="2026-04-30",
                    right="P",
                    strike=160.0,
                    quantity=-1,
                ),
                BrokerOptionPosition(
                    symbol="AMD",
                    expiry="2026-04-30",
                    right="P",
                    strike=155.0,
                    quantity=1,
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
                BrokerOptionPosition(symbol="AMD", expiry="2026-04-30", right="P", strike=160.0, quantity=-1),
                BrokerOptionPosition(symbol="AMD", expiry="2026-04-30", right="P", strike=155.0, quantity=1),
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

    assert result["status"] == "submitted"
    assert result["exit_reason"] == "take_profit"
    assert executor.calls == [("AMD", 0.42)]
    assert logger.fetch_active_managed_positions(mode="paper")[0]["status"] == "closing"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
py -3.12 -m pytest D:\Codes\trader-shawn\tests\unit\test_position_manager.py -k "manage_positions" -q
```

Expected: FAIL because `PositionManager` and the workflow helpers do not exist yet.

- [ ] **Step 3: Implement the strict manager**

Modify `D:\Codes\trader-shawn\src\trader_shawn\positions\manager.py` to add a workflow class:

```python
class PositionManager:
    def __init__(
        self,
        *,
        audit_logger: AuditLogger,
        market_data: Any,
        executor: Any,
        earnings_calendar: EarningsCalendar,
        risk_settings: Any,
        mode: str,
        as_of: date | None = None,
    ) -> None:
        self._audit_logger = audit_logger
        self._market_data = market_data
        self._executor = executor
        self._earnings_calendar = earnings_calendar
        self._risk_settings = risk_settings
        self._mode = mode
        self._as_of = as_of

    def manage_positions(self) -> dict[str, object]:
        saved_positions = self._audit_logger.fetch_active_managed_positions(mode=self._mode)
        broker_positions = self._market_data.fetch_option_positions()
        reconciled = _reconcile_positions(saved_positions, broker_positions)
        if reconciled["status"] != "ok":
            return reconciled

        for record in reconciled["positions"]:
            snapshot = _build_position_snapshot(
                record,
                market_data=self._market_data,
                as_of=self._as_of,
            )
            exit_reason = evaluate_exit(
                snapshot,
                profit_take_pct=self._risk_settings.profit_take_pct,
                stop_loss_multiple=self._risk_settings.stop_loss_multiple,
                exit_dte_threshold=self._risk_settings.exit_dte_threshold,
                earnings_calendar=self._earnings_calendar,
                as_of=self._as_of,
            )
            if exit_reason is None:
                self._audit_logger.update_managed_position(
                    record["position_id"],
                    last_known_debit=snapshot.current_debit,
                    last_evaluated_at=snapshot.updated_at.isoformat(),
                )
                continue

            submission = self._executor.submit_limit_combo(
                snapshot,
                limit_price=float(snapshot.current_debit),
            )
            self._audit_logger.update_managed_position(
                record["position_id"],
                status="closing",
                last_known_debit=snapshot.current_debit,
                last_evaluated_at=snapshot.updated_at.isoformat(),
            )
            self._audit_logger.record_position_event(
                record["position_id"],
                "close_submitted",
                {
                    "exit_reason": exit_reason,
                    "order_id": submission.get("order_id"),
                },
            )
            return {
                "status": "submitted",
                "position_id": record["position_id"],
                "ticker": record["ticker"],
                "exit_reason": exit_reason,
                "payload": submission,
            }

        return {"status": "ok", "managed_count": len(saved_positions)}
```

Add strict reconciliation helpers in the same file:

```python
def _reconcile_positions(
    saved_positions: list[dict[str, object]],
    broker_positions: list[BrokerOptionPosition],
) -> dict[str, object]:
    if not saved_positions and broker_positions:
        return {"status": "anomaly", "reason": "unknown_broker_position"}

    fingerprints = {
        _fingerprint_from_record(record): record
        for record in saved_positions
    }
    remaining = {
        _fingerprint_from_broker_positions(group): group
        for group in _group_broker_legs(broker_positions)
    }

    unknown = sorted(set(remaining) - set(fingerprints))
    if unknown:
        return {"status": "anomaly", "reason": "unknown_broker_position", "fingerprints": unknown}

    missing = sorted(set(fingerprints) - set(remaining))
    if missing:
        return {"status": "anomaly", "reason": "missing_broker_position", "fingerprints": missing}

    return {"status": "ok", "positions": list(fingerprints.values())}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```powershell
py -3.12 -m pytest D:\Codes\trader-shawn\tests\unit\test_position_manager.py -k "manage_positions" -q
```

Expected: PASS with the new workflow tests green.

- [ ] **Step 5: Commit**

Run:

```powershell
git add D:\Codes\trader-shawn\src\trader_shawn\positions\manager.py D:\Codes\trader-shawn\src\trader_shawn\monitoring\audit_logger.py D:\Codes\trader-shawn\tests\unit\test_position_manager.py
git commit -m "feat: add strict live manage workflow"
```

### Task 4: Wire The Runtime `manage` Command And Dashboard Updates

**Files:**
- Modify: `D:\Codes\trader-shawn\src\trader_shawn\app.py`
- Modify: `D:\Codes\trader-shawn\src\trader_shawn\monitoring\dashboard_api.py`
- Modify: `D:\Codes\trader-shawn\tests\integration\test_cli_commands.py`

- [ ] **Step 1: Write the failing CLI integration tests**

Add these tests to `D:\Codes\trader-shawn\tests\integration\test_cli_commands.py`:

```python
def test_manage_command_executes_real_runtime_manager(monkeypatch) -> None:
    runner = CliRunner()
    manager = SimpleNamespace(
        manage_positions=lambda: {
            "status": "submitted",
            "position_id": "pos-1",
            "ticker": "AMD",
            "exit_reason": "take_profit",
            "payload": {"order_id": 404},
        }
    )
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: SimpleNamespace(
            settings=_settings(),
            config_dir=(Path.cwd() / "config").resolve(),
            position_manager=manager,
            dashboard_state_path=None,
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["manage"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "submitted"
    assert payload["exit_reason"] == "take_profit"


def test_manage_command_updates_dashboard_snapshot(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    state_path = tmp_path / "dashboard.json"
    monkeypatch.setattr(
        app_module,
        "build_cli_runtime",
        lambda: SimpleNamespace(
            settings=_settings(),
            config_dir=(Path.cwd() / "config").resolve(),
            position_manager=SimpleNamespace(
                manage_positions=lambda: {"status": "ok", "managed_count": 1}
            ),
            dashboard_state_path=state_path,
        ),
        raising=False,
    )

    result = runner.invoke(cli, ["manage"])

    assert result.exit_code == 0
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "status": "updated",
        "last_cycle": {"status": "ok"},
        "error": None,
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
py -3.12 -m pytest D:\Codes\trader-shawn\tests\integration\test_cli_commands.py -k "manage_command_executes_real_runtime_manager or manage_command_updates_dashboard_snapshot" -q
```

Expected: FAIL because the current command path does not update the dashboard and the default runtime does not yet build a real manager.

- [ ] **Step 3: Implement the runtime wiring**

Modify `D:\Codes\trader-shawn\src\trader_shawn\monitoring\dashboard_api.py` so `manage` results normalize the same way as entry workflows:

```python
def _normalize_last_cycle(last_cycle: Any) -> dict[str, Any]:
    if not isinstance(last_cycle, dict):
        return {}

    normalized: dict[str, Any] = {}
    for key in (
        "status",
        "reason",
        "action",
        "error_type",
        "message",
        "ticker",
        "position_id",
        "exit_reason",
        "managed_count",
    ):
        value = last_cycle.get(key)
        if isinstance(value, str) and value:
            normalized[key] = value
        elif isinstance(value, int):
            normalized[key] = value
```

Modify `D:\Codes\trader-shawn\src\trader_shawn\app.py` to build the manager:

```python
from trader_shawn.monitoring.audit_logger import AuditLogger
from trader_shawn.positions.manager import PositionManager


def build_cli_runtime() -> CliRuntime:
    config_dir = _default_config_dir()
    settings = load_settings(config_dir)
    market_data_client = IbkrMarketDataClient(
        host=settings.ibkr.host,
        port=settings.ibkr.port,
        client_id=settings.ibkr.client_id,
    )
    earnings_calendar = EarningsCalendar(settings.events)
    audit_logger = AuditLogger(settings.audit_db_path)
    executor = IbkrExecutor(
        host=settings.ibkr.host,
        port=settings.ibkr.port,
        client_id=settings.ibkr.client_id,
    )
    position_manager = PositionManager(
        audit_logger=audit_logger,
        market_data=market_data_client,
        executor=executor,
        earnings_calendar=earnings_calendar,
        risk_settings=settings.risk,
        mode=settings.mode,
    )
    return CliRuntime(
        ...
        executor=executor,
        position_manager=position_manager,
        dashboard_state_path=(config_dir.parent / "runtime" / "dashboard.json").resolve(),
    )
```

Also update `_manage_command()` to call `_update_dashboard_snapshot(runtime, payload)` before returning.

- [ ] **Step 4: Run the integration tests and the full suite**

Run:

```powershell
py -3.12 -m pytest D:\Codes\trader-shawn\tests\integration\test_cli_commands.py -k "manage_command_executes_real_runtime_manager or manage_command_updates_dashboard_snapshot" -q
py -3.12 -m pytest D:\Codes\trader-shawn\tests -q
```

Expected: PASS with the new `manage` integration tests included in the full suite.

- [ ] **Step 5: Commit**

Run:

```powershell
git add D:\Codes\trader-shawn\src\trader_shawn\app.py D:\Codes\trader-shawn\src\trader_shawn\monitoring\dashboard_api.py D:\Codes\trader-shawn\tests\integration\test_cli_commands.py
git commit -m "feat: wire runtime manage command"
```

## Self-Review

Spec coverage:

- SQLite as source of truth: covered in Task 1.
- Strict state-driven reconciliation: covered in Task 3.
- Unknown broker positions fail closed: covered in Task 3 tests and reconciliation logic.
- Live debit estimation and broker position fetch: covered in Task 2.
- Close combo submission and lifecycle persistence: covered in Tasks 2 and 3.
- CLI and dashboard runtime wiring: covered in Task 4.

Placeholder scan:

- No unfinished implementation markers remain.
- Every code-changing step includes concrete code or method signatures.

Type consistency:

- `ManagedPositionRecord` and `BrokerOptionPosition` are defined in Task 1 and reused in Tasks 2-4.
- `PositionManager.manage_positions()` returns dict payloads compatible with `_manage_command()` and dashboard normalization.

Plan complete and saved to `docs/superpowers/plans/2026-04-20-manage-live-position-reconstruction.md`.

Execution options remain:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

The current thread already chose Subagent-Driven, so execute this plan that way unless the user explicitly switches modes.
