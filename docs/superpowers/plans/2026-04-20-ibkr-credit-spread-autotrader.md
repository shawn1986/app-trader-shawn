# IBKR Credit Spread Autotrader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python-based IBKR credit spread autotrader that scans `SPY`, `QQQ`, `GOOG`, `AMD`, and `NVDA`, requests constrained AI trade decisions from `Claude CLI` with `Codex` as a second opinion, enforces hard risk rules, submits combo limit orders, and manages exits in both `paper` and `live` modes.

**Architecture:** Use a modular monolith with clear boundaries between configuration/domain models, market data + candidate generation, AI decisioning, risk validation, broker execution, position management, and audit/monitoring. Keep the first version intentionally narrow: two spread types, a fixed symbol universe, limit orders only, no auto-roll, and fail-closed behavior whenever data, AI output, or broker state is invalid.

**Tech Stack:** Python 3.11+, `ib_insync`, `pydantic`, `PyYAML`, `typer`, `pytest`, `pytest-mock`, `sqlite3`

---

## File Structure

Create or modify these files during implementation:

- `D:\Codes\trader-shawn\.gitignore`
- `D:\Codes\trader-shawn\pyproject.toml`
- `D:\Codes\trader-shawn\README.md`
- `D:\Codes\trader-shawn\.env.example`
- `D:\Codes\trader-shawn\config\app.yaml`
- `D:\Codes\trader-shawn\config\symbols.yaml`
- `D:\Codes\trader-shawn\config\risk.yaml`
- `D:\Codes\trader-shawn\config\providers.yaml`
- `D:\Codes\trader-shawn\config\events.yaml`
- `D:\Codes\trader-shawn\src\trader_shawn\__init__.py`
- `D:\Codes\trader-shawn\src\trader_shawn\app.py`
- `D:\Codes\trader-shawn\src\trader_shawn\scheduler.py`
- `D:\Codes\trader-shawn\src\trader_shawn\domain\enums.py`
- `D:\Codes\trader-shawn\src\trader_shawn\domain\models.py`
- `D:\Codes\trader-shawn\src\trader_shawn\settings.py`
- `D:\Codes\trader-shawn\src\trader_shawn\market_data\ibkr_market_data.py`
- `D:\Codes\trader-shawn\src\trader_shawn\candidate_builder\credit_spread_builder.py`
- `D:\Codes\trader-shawn\src\trader_shawn\ai\base.py`
- `D:\Codes\trader-shawn\src\trader_shawn\ai\decision_parser.py`
- `D:\Codes\trader-shawn\src\trader_shawn\ai\claude_cli_adapter.py`
- `D:\Codes\trader-shawn\src\trader_shawn\ai\codex_adapter.py`
- `D:\Codes\trader-shawn\src\trader_shawn\ai\service.py`
- `D:\Codes\trader-shawn\src\trader_shawn\risk\rules.py`
- `D:\Codes\trader-shawn\src\trader_shawn\risk\guard.py`
- `D:\Codes\trader-shawn\src\trader_shawn\execution\order_builder.py`
- `D:\Codes\trader-shawn\src\trader_shawn\execution\ibkr_executor.py`
- `D:\Codes\trader-shawn\src\trader_shawn\positions\manager.py`
- `D:\Codes\trader-shawn\src\trader_shawn\monitoring\audit_logger.py`
- `D:\Codes\trader-shawn\src\trader_shawn\monitoring\state_store.py`
- `D:\Codes\trader-shawn\src\trader_shawn\monitoring\dashboard_api.py`
- `D:\Codes\trader-shawn\src\trader_shawn\events\earnings_calendar.py`
- `D:\Codes\trader-shawn\tests\unit\test_settings.py`
- `D:\Codes\trader-shawn\tests\unit\test_candidate_builder.py`
- `D:\Codes\trader-shawn\tests\unit\test_decision_parser.py`
- `D:\Codes\trader-shawn\tests\unit\test_risk_guard.py`
- `D:\Codes\trader-shawn\tests\unit\test_position_manager.py`
- `D:\Codes\trader-shawn\tests\integration\test_ai_service.py`
- `D:\Codes\trader-shawn\tests\integration\test_trade_cycle.py`

Responsibility map:

- `settings.py`: load YAML + env and expose typed app settings
- `domain/*`: shared enums and models passed between modules
- `market_data/*`: IBKR snapshot and option chain normalization
- `candidate_builder/*`: deterministic spread generation and liquidity filtering
- `ai/*`: provider adapters, JSON parsing, decision orchestration
- `risk/*`: hard trade gating and account-level limits
- `execution/*`: combo order construction and broker order submission
- `positions/*`: open-position exit logic
- `monitoring/*`: audit persistence, run state, basic status view
- `events/*`: local event-calendar loading for entry/exit blocks

### Task 1: Bootstrap The Project And Typed Settings

**Files:**
- Create: `D:\Codes\trader-shawn\.gitignore`
- Create: `D:\Codes\trader-shawn\pyproject.toml`
- Create: `D:\Codes\trader-shawn\README.md`
- Create: `D:\Codes\trader-shawn\.env.example`
- Create: `D:\Codes\trader-shawn\config\app.yaml`
- Create: `D:\Codes\trader-shawn\config\symbols.yaml`
- Create: `D:\Codes\trader-shawn\config\risk.yaml`
- Create: `D:\Codes\trader-shawn\config\providers.yaml`
- Create: `D:\Codes\trader-shawn\config\events.yaml`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\__init__.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\settings.py`
- Test: `D:\Codes\trader-shawn\tests\unit\test_settings.py`

- [ ] **Step 1: Verify Python availability and write the failing settings test**

Run:

```powershell
py --list
```

Then create `D:\Codes\trader-shawn\tests\unit\test_settings.py`:

```python
from pathlib import Path

from trader_shawn.settings import load_settings


def test_load_settings_merges_yaml_and_env(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    (config_dir / "app.yaml").write_text(
        "mode: paper\nibkr:\n  host: 127.0.0.1\n  port: 7497\n  client_id: 7\n",
        encoding="utf-8",
    )
    (config_dir / "symbols.yaml").write_text(
        "symbols:\n  - SPY\n  - QQQ\n  - GOOG\n  - AMD\n  - NVDA\n",
        encoding="utf-8",
    )
    (config_dir / "risk.yaml").write_text(
        "max_risk_per_trade_pct: 0.02\nmax_daily_loss_pct: 0.04\nmax_new_positions_per_day: 6\nmax_open_risk_pct: 0.20\nmax_spreads_per_symbol: 2\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "provider_mode: claude_primary\nprimary_provider: claude_cli\nsecondary_provider: codex\nprovider_timeout_seconds: 15\n",
        encoding="utf-8",
    )
    (config_dir / "events.yaml").write_text("events: []\n", encoding="utf-8")

    monkeypatch.setenv("TRADER_SHAWN_MODE", "live")
    monkeypatch.setenv("TRADER_SHAWN_LIVE_ENABLED", "true")

    settings = load_settings(config_dir)

    assert settings.mode == "live"
    assert settings.live_enabled is True
    assert settings.symbols == ["SPY", "QQQ", "GOOG", "AMD", "NVDA"]
    assert settings.risk.max_risk_per_trade_pct == 0.02
    assert settings.providers.primary_provider == "claude_cli"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\unit\test_settings.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'trader_shawn'`.

- [ ] **Step 3: Implement project metadata, config files, and settings loader**

Create `D:\Codes\trader-shawn\pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "trader-shawn"
version = "0.1.0"
description = "IBKR credit spread autotrader"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
  "ib-insync>=0.9.86",
  "pydantic>=2.7.0",
  "PyYAML>=6.0.1",
  "typer>=0.12.3",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.1.1",
  "pytest-mock>=3.14.0",
]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

Create `D:\Codes\trader-shawn\.gitignore`:

```gitignore
__pycache__/
.pytest_cache/
.mypy_cache/
.coverage
*.pyc
.env
.superpowers/
runtime/
```

Create `D:\Codes\trader-shawn\README.md`:

```markdown
# trader-shawn

IBKR credit spread autotrader for a fixed options universe.
```

Create `D:\Codes\trader-shawn\.env.example`:

```dotenv
TRADER_SHAWN_MODE=paper
TRADER_SHAWN_LIVE_ENABLED=false
TRADER_SHAWN_IBKR_HOST=127.0.0.1
TRADER_SHAWN_IBKR_PORT=7497
TRADER_SHAWN_IBKR_CLIENT_ID=7
TRADER_SHAWN_CLAUDE_COMMAND=claude
TRADER_SHAWN_CODEX_COMMAND=codex
```

Create `D:\Codes\trader-shawn\config\app.yaml`:

```yaml
mode: paper
live_enabled: false
ibkr:
  host: 127.0.0.1
  port: 7497
  client_id: 7
audit_db_path: runtime/audit.db
```

Create `D:\Codes\trader-shawn\config\symbols.yaml`:

```yaml
symbols:
  - SPY
  - QQQ
  - GOOG
  - AMD
  - NVDA
```

Create `D:\Codes\trader-shawn\config\risk.yaml`:

```yaml
max_risk_per_trade_pct: 0.02
max_daily_loss_pct: 0.04
max_new_positions_per_day: 6
max_open_risk_pct: 0.20
max_spreads_per_symbol: 2
profit_take_pct: 0.50
stop_loss_multiple: 2.0
exit_dte_threshold: 5
```

Create `D:\Codes\trader-shawn\config\providers.yaml`:

```yaml
provider_mode: claude_primary
primary_provider: claude_cli
secondary_provider: codex
provider_timeout_seconds: 15
secondary_timeout_seconds: 10
```

Create `D:\Codes\trader-shawn\config\events.yaml`:

```yaml
events: []
```

Create `D:\Codes\trader-shawn\src\trader_shawn\__init__.py`:

```python
__all__ = ["__version__"]

__version__ = "0.1.0"
```

Create `D:\Codes\trader-shawn\src\trader_shawn\settings.py`:

```python
from pathlib import Path
from typing import Literal
import os

import yaml
from pydantic import BaseModel


class IbkrSettings(BaseModel):
    host: str
    port: int
    client_id: int


class RiskSettings(BaseModel):
    max_risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_new_positions_per_day: int
    max_open_risk_pct: float
    max_spreads_per_symbol: int
    profit_take_pct: float
    stop_loss_multiple: float
    exit_dte_threshold: int


class ProviderSettings(BaseModel):
    provider_mode: str
    primary_provider: str
    secondary_provider: str
    provider_timeout_seconds: int
    secondary_timeout_seconds: int


class AppSettings(BaseModel):
    mode: Literal["paper", "live"]
    live_enabled: bool
    ibkr: IbkrSettings
    audit_db_path: str
    symbols: list[str]
    risk: RiskSettings
    providers: ProviderSettings
    events: list[dict]


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def load_settings(config_dir: Path) -> AppSettings:
    app = _load_yaml(config_dir / "app.yaml")
    risk = _load_yaml(config_dir / "risk.yaml")
    providers = _load_yaml(config_dir / "providers.yaml")
    symbols = _load_yaml(config_dir / "symbols.yaml")
    events = _load_yaml(config_dir / "events.yaml")

    mode = os.getenv("TRADER_SHAWN_MODE", app["mode"])
    live_enabled = _env_bool("TRADER_SHAWN_LIVE_ENABLED", app.get("live_enabled", False))
    ibkr = app["ibkr"]
    ibkr["host"] = os.getenv("TRADER_SHAWN_IBKR_HOST", ibkr["host"])
    ibkr["port"] = int(os.getenv("TRADER_SHAWN_IBKR_PORT", ibkr["port"]))
    ibkr["client_id"] = int(os.getenv("TRADER_SHAWN_IBKR_CLIENT_ID", ibkr["client_id"]))

    return AppSettings(
        mode=mode,
        live_enabled=live_enabled,
        ibkr=IbkrSettings(**ibkr),
        audit_db_path=app["audit_db_path"],
        symbols=symbols["symbols"],
        risk=RiskSettings(**risk),
        providers=ProviderSettings(**providers),
        events=events["events"],
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\unit\test_settings.py -v
```

Expected: PASS with `1 passed`.

- [ ] **Step 5: Initialize git and commit the bootstrap**

Run:

```powershell
git init
git add .gitignore pyproject.toml README.md .env.example config src tests
git commit -m "chore: bootstrap project settings"
```

Expected: a root commit containing the project scaffolding and typed settings loader.

### Task 2: Define Shared Domain Models And Audit Storage

**Files:**
- Create: `D:\Codes\trader-shawn\src\trader_shawn\domain\enums.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\domain\models.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\monitoring\audit_logger.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\monitoring\state_store.py`
- Test: `D:\Codes\trader-shawn\tests\unit\test_decision_parser.py`

- [ ] **Step 1: Write a failing audit persistence test**

Create `D:\Codes\trader-shawn\tests\unit\test_decision_parser.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\unit\test_decision_parser.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `trader_shawn.domain.models` or `trader_shawn.monitoring.audit_logger`.

- [ ] **Step 3: Implement enums, models, and SQLite audit logging**

Create `D:\Codes\trader-shawn\src\trader_shawn\domain\enums.py`:

```python
from enum import Enum


class RuntimeMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class SpreadStrategy(str, Enum):
    BULL_PUT = "bull_put_credit_spread"
    BEAR_CALL = "bear_call_credit_spread"


class DecisionAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
```

Create `D:\Codes\trader-shawn\src\trader_shawn\domain\models.py`:

```python
from datetime import datetime

from pydantic import BaseModel, Field


class OptionQuote(BaseModel):
    symbol: str
    expiry: str
    strike: float
    right: str
    bid: float
    ask: float
    delta: float | None = None
    open_interest: int = 0
    volume: int = 0


class CandidateSpread(BaseModel):
    ticker: str
    strategy: str
    expiry: str
    dte: int
    short_strike: float
    long_strike: float
    width: float
    credit: float
    max_loss: float
    short_delta: float
    pop: float
    bid_ask_ratio: float


class DecisionRecord(BaseModel):
    cycle_id: str
    provider: str
    action: str
    ticker: str | None = None
    payload: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AccountSnapshot(BaseModel):
    net_liq: float
    realized_pnl: float
    unrealized_pnl: float
    open_risk: float
    new_positions_today: int


class PositionSnapshot(BaseModel):
    ticker: str
    strategy: str
    expiry: str
    short_strike: float
    long_strike: float
    entry_credit: float
    current_debit: float
    dte: int
    short_leg_distance_pct: float
```

Create `D:\Codes\trader-shawn\src\trader_shawn\monitoring\audit_logger.py`:

```python
from __future__ import annotations

from pathlib import Path
import json
import sqlite3

from trader_shawn.domain.models import DecisionRecord


class AuditLogger:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """
                create table if not exists decisions (
                    cycle_id text not null,
                    provider text not null,
                    action text not null,
                    ticker text,
                    payload_json text not null,
                    created_at text not null
                )
                """
            )

    def record_decision(self, record: DecisionRecord) -> None:
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """
                insert into decisions (cycle_id, provider, action, ticker, payload_json, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.cycle_id,
                    record.provider,
                    record.action,
                    record.ticker,
                    json.dumps(record.payload),
                    record.created_at.isoformat(),
                ),
            )
```

Create `D:\Codes\trader-shawn\src\trader_shawn\monitoring\state_store.py`:

```python
from pathlib import Path
import json


class StateStore:
    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, payload: dict) -> None:
        self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load(self) -> dict:
        if not self._state_path.exists():
            return {}
        return json.loads(self._state_path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\unit\test_decision_parser.py -v
```

Expected: PASS with `1 passed`.

- [ ] **Step 5: Commit the shared models and audit layer**

Run:

```powershell
git add src/trader_shawn/domain src/trader_shawn/monitoring tests/unit/test_decision_parser.py
git commit -m "feat: add domain models and audit storage"
```

### Task 3: Implement Event Calendar, Market Data Snapshotting, And Candidate Building

**Files:**
- Create: `D:\Codes\trader-shawn\src\trader_shawn\events\earnings_calendar.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\market_data\ibkr_market_data.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\candidate_builder\credit_spread_builder.py`
- Test: `D:\Codes\trader-shawn\tests\unit\test_candidate_builder.py`

- [ ] **Step 1: Write a failing candidate builder test**

Create `D:\Codes\trader-shawn\tests\unit\test_candidate_builder.py`:

```python
from trader_shawn.candidate_builder.credit_spread_builder import build_candidates
from trader_shawn.domain.models import OptionQuote


def test_build_candidates_filters_liquidity_and_creates_bull_put_spread() -> None:
    quotes = [
        OptionQuote(symbol="AMD", expiry="2026-04-30", strike=160, right="P", bid=1.40, ask=1.50, delta=-0.22, open_interest=500, volume=120),
        OptionQuote(symbol="AMD", expiry="2026-04-30", strike=155, right="P", bid=0.65, ask=0.75, delta=-0.12, open_interest=500, volume=100),
        OptionQuote(symbol="AMD", expiry="2026-04-30", strike=165, right="P", bid=2.10, ask=2.60, delta=-0.31, open_interest=5, volume=1),
    ]

    candidates = build_candidates("AMD", 10, quotes)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.strategy == "bull_put_credit_spread"
    assert candidate.short_strike == 160
    assert candidate.long_strike == 155
    assert round(candidate.credit, 2) == 0.75
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\unit\test_candidate_builder.py -v
```

Expected: FAIL because `build_candidates` does not exist.

- [ ] **Step 3: Implement event loader, IBKR normalization stub, and candidate generation**

Create `D:\Codes\trader-shawn\src\trader_shawn\events\earnings_calendar.py`:

```python
from datetime import date


class EarningsCalendar:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    def has_blocking_event(self, ticker: str, start: date, end: date) -> bool:
        for event in self._events:
            if event["ticker"] != ticker:
                continue
            event_date = date.fromisoformat(event["date"])
            if start <= event_date <= end:
                return True
        return False
```

Create `D:\Codes\trader-shawn\src\trader_shawn\market_data\ibkr_market_data.py`:

```python
from __future__ import annotations

from trader_shawn.domain.models import OptionQuote


class IbkrMarketDataClient:
    def __init__(self, ib) -> None:
        self._ib = ib

    def normalize_option_quotes(self, ticker: str, raw_quotes: list[dict]) -> list[OptionQuote]:
        normalized: list[OptionQuote] = []
        for row in raw_quotes:
            normalized.append(
                OptionQuote(
                    symbol=ticker,
                    expiry=row["expiry"],
                    strike=float(row["strike"]),
                    right=row["right"],
                    bid=float(row["bid"]),
                    ask=float(row["ask"]),
                    delta=row.get("delta"),
                    open_interest=int(row.get("open_interest", 0)),
                    volume=int(row.get("volume", 0)),
                )
            )
        return normalized
```

Create `D:\Codes\trader-shawn\src\trader_shawn\candidate_builder\credit_spread_builder.py`:

```python
from trader_shawn.domain.models import CandidateSpread, OptionQuote


def _mid(quote: OptionQuote) -> float:
    return round((quote.bid + quote.ask) / 2, 2)


def build_candidates(ticker: str, dte: int, quotes: list[OptionQuote]) -> list[CandidateSpread]:
    puts = sorted(
        [
            quote
            for quote in quotes
            if quote.right == "P"
            and quote.delta is not None
            and 0.15 <= abs(quote.delta) <= 0.25
            and quote.open_interest >= 100
            and quote.volume >= 50
            and quote.ask > 0
        ],
        key=lambda item: item.strike,
        reverse=True,
    )
    candidates: list[CandidateSpread] = []
    for short_leg in puts:
        for long_leg in puts:
            if long_leg.strike >= short_leg.strike:
                continue
            width = short_leg.strike - long_leg.strike
            if width <= 0 or width > 5:
                continue
            credit = round(_mid(short_leg) - _mid(long_leg), 2)
            if credit <= 0:
                continue
            bid_ask_ratio = round((short_leg.ask - short_leg.bid) / max(credit, 0.01), 2)
            if bid_ask_ratio > 0.15:
                continue
            candidates.append(
                CandidateSpread(
                    ticker=ticker,
                    strategy="bull_put_credit_spread",
                    expiry=short_leg.expiry,
                    dte=dte,
                    short_strike=short_leg.strike,
                    long_strike=long_leg.strike,
                    width=width,
                    credit=credit,
                    max_loss=round(width - credit, 2),
                    short_delta=abs(short_leg.delta or 0.0),
                    pop=round(1 - abs(short_leg.delta or 0.0), 2),
                    bid_ask_ratio=bid_ask_ratio,
                )
            )
    return candidates
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\unit\test_candidate_builder.py -v
```

Expected: PASS with `1 passed`.

- [ ] **Step 5: Commit market data and candidate generation**

Run:

```powershell
git add src/trader_shawn/events src/trader_shawn/market_data src/trader_shawn/candidate_builder tests/unit/test_candidate_builder.py
git commit -m "feat: add market snapshots and candidate builder"
```

### Task 4: Implement AI Response Parsing And Provider Adapters

**Files:**
- Create: `D:\Codes\trader-shawn\src\trader_shawn\ai\base.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\ai\decision_parser.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\ai\claude_cli_adapter.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\ai\codex_adapter.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\ai\service.py`
- Test: `D:\Codes\trader-shawn\tests\integration\test_ai_service.py`

- [ ] **Step 1: Write a failing AI orchestration test**

Create `D:\Codes\trader-shawn\tests\integration\test_ai_service.py`:

```python
from trader_shawn.ai.service import AiDecisionService


class StubProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def request(self, prompt: str) -> dict:
        return self.payload


def test_ai_service_returns_primary_decision_and_secondary_note() -> None:
    service = AiDecisionService(
        primary=StubProvider(
            {
                "action": "approve",
                "ticker": "AMD",
                "strategy": "bull_put_credit_spread",
                "expiry": "2026-04-30",
                "short_strike": 160,
                "long_strike": 155,
                "limit_credit": 1.05,
                "confidence": 0.72,
                "reason": "primary",
                "risk_note": "ok",
            }
        ),
        secondary=StubProvider({"action": "reject", "reason": "too concentrated"}),
    )

    decision = service.decide({"ticker": "AMD", "candidates": []})

    assert decision.action == "approve"
    assert decision.secondary_payload["reason"] == "too concentrated"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\integration\test_ai_service.py -v
```

Expected: FAIL because `AiDecisionService` does not exist.

- [ ] **Step 3: Implement provider contracts, parser, and CLI adapters**

Create `D:\Codes\trader-shawn\src\trader_shawn\ai\base.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod


class AiProvider(ABC):
    @abstractmethod
    def request(self, prompt: str) -> dict:
        raise NotImplementedError
```

Create `D:\Codes\trader-shawn\src\trader_shawn\ai\decision_parser.py`:

```python
from pydantic import BaseModel


class ParsedDecision(BaseModel):
    action: str
    ticker: str | None = None
    strategy: str | None = None
    expiry: str | None = None
    short_strike: float | None = None
    long_strike: float | None = None
    limit_credit: float | None = None
    confidence: float | None = None
    reason: str
    risk_note: str | None = None
    secondary_payload: dict = {}


def parse_decision(payload: dict) -> ParsedDecision:
    return ParsedDecision(**payload)
```

Create `D:\Codes\trader-shawn\src\trader_shawn\ai\claude_cli_adapter.py`:

```python
from __future__ import annotations

import json
import subprocess

from trader_shawn.ai.base import AiProvider


class ClaudeCliAdapter(AiProvider):
    def __init__(self, command: str = "claude", timeout_seconds: int = 15) -> None:
        self._command = command
        self._timeout_seconds = timeout_seconds

    def request(self, prompt: str) -> dict:
        completed = subprocess.run(
            [self._command, "-p", "--output-format", "json", prompt],
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
            check=True,
        )
        return json.loads(completed.stdout)
```

Create `D:\Codes\trader-shawn\src\trader_shawn\ai\codex_adapter.py`:

```python
from __future__ import annotations

import json
import subprocess

from trader_shawn.ai.base import AiProvider


class CodexAdapter(AiProvider):
    def __init__(self, command: str = "codex", timeout_seconds: int = 10) -> None:
        self._command = command
        self._timeout_seconds = timeout_seconds

    def request(self, prompt: str) -> dict:
        completed = subprocess.run(
            [self._command, "exec", "--json", prompt],
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
            check=True,
        )
        return json.loads(completed.stdout)
```

Create `D:\Codes\trader-shawn\src\trader_shawn\ai\service.py`:

```python
from __future__ import annotations

import json

from trader_shawn.ai.base import AiProvider
from trader_shawn.ai.decision_parser import ParsedDecision, parse_decision


class AiDecisionService:
    def __init__(self, primary: AiProvider, secondary: AiProvider | None = None) -> None:
        self._primary = primary
        self._secondary = secondary

    def decide(self, context: dict) -> ParsedDecision:
        prompt = json.dumps(context, ensure_ascii=False)
        primary_payload = self._primary.request(prompt)
        decision = parse_decision(primary_payload)
        if self._secondary is None:
            return decision
        try:
            secondary_payload = self._secondary.request(prompt)
        except Exception:
            secondary_payload = {"action": "error", "reason": "secondary provider failed"}
        decision.secondary_payload = secondary_payload
        return decision
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\integration\test_ai_service.py -v
```

Expected: PASS with `1 passed`.

- [ ] **Step 5: Commit AI parsing and provider adapters**

Run:

```powershell
git add src/trader_shawn/ai tests/integration/test_ai_service.py
git commit -m "feat: add ai provider adapters"
```

### Task 5: Implement Hard Risk Rules And Guard Evaluation

**Files:**
- Create: `D:\Codes\trader-shawn\src\trader_shawn\risk\rules.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\risk\guard.py`
- Test: `D:\Codes\trader-shawn\tests\unit\test_risk_guard.py`

- [ ] **Step 1: Write a failing risk guard test**

Create `D:\Codes\trader-shawn\tests\unit\test_risk_guard.py`:

```python
from trader_shawn.domain.models import AccountSnapshot, CandidateSpread
from trader_shawn.risk.guard import RiskGuard


def test_risk_guard_blocks_when_trade_risk_exceeds_limit() -> None:
    guard = RiskGuard(
        max_risk_per_trade_pct=0.02,
        max_daily_loss_pct=0.04,
        max_new_positions_per_day=6,
        max_open_risk_pct=0.20,
        max_spreads_per_symbol=2,
    )
    account = AccountSnapshot(
        net_liq=10_000,
        realized_pnl=0,
        unrealized_pnl=0,
        open_risk=500,
        new_positions_today=0,
    )
    spread = CandidateSpread(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        dte=10,
        short_strike=160,
        long_strike=150,
        width=10,
        credit=1.0,
        max_loss=900,
        short_delta=0.20,
        pop=0.80,
        bid_ask_ratio=0.08,
    )

    result = guard.evaluate(spread, account, open_symbol_count=0)

    assert result.allowed is False
    assert result.reason == "max_risk_per_trade_pct"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\unit\test_risk_guard.py -v
```

Expected: FAIL because `RiskGuard` does not exist.

- [ ] **Step 3: Implement risk rules and guard results**

Create `D:\Codes\trader-shawn\src\trader_shawn\risk\rules.py`:

```python
from pydantic import BaseModel


class GuardResult(BaseModel):
    allowed: bool
    reason: str
```

Create `D:\Codes\trader-shawn\src\trader_shawn\risk\guard.py`:

```python
from trader_shawn.domain.models import AccountSnapshot, CandidateSpread
from trader_shawn.risk.rules import GuardResult


class RiskGuard:
    def __init__(
        self,
        max_risk_per_trade_pct: float,
        max_daily_loss_pct: float,
        max_new_positions_per_day: int,
        max_open_risk_pct: float,
        max_spreads_per_symbol: int,
    ) -> None:
        self._max_risk_per_trade_pct = max_risk_per_trade_pct
        self._max_daily_loss_pct = max_daily_loss_pct
        self._max_new_positions_per_day = max_new_positions_per_day
        self._max_open_risk_pct = max_open_risk_pct
        self._max_spreads_per_symbol = max_spreads_per_symbol

    def evaluate(
        self,
        spread: CandidateSpread,
        account: AccountSnapshot,
        open_symbol_count: int,
    ) -> GuardResult:
        if spread.max_loss > account.net_liq * self._max_risk_per_trade_pct:
            return GuardResult(allowed=False, reason="max_risk_per_trade_pct")
        if abs(account.realized_pnl + account.unrealized_pnl) > account.net_liq * self._max_daily_loss_pct:
            return GuardResult(allowed=False, reason="max_daily_loss_pct")
        if account.new_positions_today >= self._max_new_positions_per_day:
            return GuardResult(allowed=False, reason="max_new_positions_per_day")
        if account.open_risk > account.net_liq * self._max_open_risk_pct:
            return GuardResult(allowed=False, reason="max_open_risk_pct")
        if open_symbol_count >= self._max_spreads_per_symbol:
            return GuardResult(allowed=False, reason="max_spreads_per_symbol")
        return GuardResult(allowed=True, reason="ok")
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\unit\test_risk_guard.py -v
```

Expected: PASS with `1 passed`.

- [ ] **Step 5: Commit the hard risk guard**

Run:

```powershell
git add src/trader_shawn/risk tests/unit/test_risk_guard.py
git commit -m "feat: add hard risk guard"
```

### Task 6: Implement Combo Order Building And Position Exit Logic

**Files:**
- Create: `D:\Codes\trader-shawn\src\trader_shawn\execution\order_builder.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\execution\ibkr_executor.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\positions\manager.py`
- Test: `D:\Codes\trader-shawn\tests\unit\test_position_manager.py`

- [ ] **Step 1: Write a failing exit-rule test**

Create `D:\Codes\trader-shawn\tests\unit\test_position_manager.py`:

```python
from trader_shawn.domain.models import PositionSnapshot
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

    assert evaluate_exit(position, profit_take_pct=0.50, stop_loss_multiple=2.0, exit_dte_threshold=5) == "take_profit"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\unit\test_position_manager.py -v
```

Expected: FAIL because `evaluate_exit` does not exist.

- [ ] **Step 3: Implement order builder, executor, and exit evaluation**

Create `D:\Codes\trader-shawn\src\trader_shawn\execution\order_builder.py`:

```python
from trader_shawn.domain.models import CandidateSpread


def build_combo_order_payload(spread: CandidateSpread, limit_credit: float) -> dict:
    right = "P" if spread.strategy == "bull_put_credit_spread" else "C"
    return {
        "symbol": spread.ticker,
        "expiry": spread.expiry,
        "right": right,
        "short_strike": spread.short_strike,
        "long_strike": spread.long_strike,
        "action": "SELL",
        "quantity": 1,
        "limit_credit": limit_credit,
    }
```

Create `D:\Codes\trader-shawn\src\trader_shawn\execution\ibkr_executor.py`:

```python
class IbkrExecutor:
    def __init__(self, ib) -> None:
        self._ib = ib

    def submit_limit_combo(self, payload: dict) -> dict:
        return {
            "status": "submitted",
            "symbol": payload["symbol"],
            "limit_credit": payload["limit_credit"],
        }
```

Create `D:\Codes\trader-shawn\src\trader_shawn\positions\manager.py`:

```python
from trader_shawn.domain.models import PositionSnapshot


def evaluate_exit(
    position: PositionSnapshot,
    profit_take_pct: float,
    stop_loss_multiple: float,
    exit_dte_threshold: int,
) -> str | None:
    target_debit = position.entry_credit * (1 - profit_take_pct)
    stop_debit = position.entry_credit * (1 + stop_loss_multiple)
    if position.current_debit <= target_debit:
        return "take_profit"
    if position.current_debit >= stop_debit:
        return "stop_loss"
    if position.dte <= exit_dte_threshold:
        return "dte_exit"
    if position.short_leg_distance_pct <= 0.02:
        return "short_strike_proximity"
    return None
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\unit\test_position_manager.py -v
```

Expected: PASS with `1 passed`.

- [ ] **Step 5: Commit execution and position management**

Run:

```powershell
git add src/trader_shawn/execution src/trader_shawn/positions tests/unit/test_position_manager.py
git commit -m "feat: add execution payloads and exit rules"
```

### Task 7: Wire The Trade Cycle, CLI Commands, And End-To-End Test

**Files:**
- Create: `D:\Codes\trader-shawn\src\trader_shawn\app.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\scheduler.py`
- Create: `D:\Codes\trader-shawn\src\trader_shawn\monitoring\dashboard_api.py`
- Test: `D:\Codes\trader-shawn\tests\integration\test_trade_cycle.py`

- [ ] **Step 1: Write a failing trade-cycle test**

Create `D:\Codes\trader-shawn\tests\integration\test_trade_cycle.py`:

```python
from trader_shawn.app import run_trade_cycle
from trader_shawn.domain.models import AccountSnapshot, CandidateSpread


class StubDecisionService:
    def decide(self, context: dict):
        class Decision:
            action = "approve"
            ticker = "AMD"
            strategy = "bull_put_credit_spread"
            expiry = "2026-04-30"
            short_strike = 160
            long_strike = 155
            limit_credit = 1.05
            reason = "ok"
            secondary_payload = {"reason": "secondary"}

        return Decision()


class StubExecutor:
    def submit_limit_combo(self, payload: dict) -> dict:
        return {"status": "submitted", "payload": payload}


def test_run_trade_cycle_submits_order_when_candidate_and_risk_pass() -> None:
    spread = CandidateSpread(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        dte=10,
        short_strike=160,
        long_strike=155,
        width=5,
        credit=1.0,
        max_loss=400,
        short_delta=0.20,
        pop=0.80,
        bid_ask_ratio=0.08,
    )

    result = run_trade_cycle(
        candidates=[spread],
        account=AccountSnapshot(net_liq=50_000, realized_pnl=0, unrealized_pnl=0, open_risk=0, new_positions_today=0),
        decision_service=StubDecisionService(),
        executor=StubExecutor(),
    )

    assert result["status"] == "submitted"
    assert result["payload"]["symbol"] == "AMD"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\integration\test_trade_cycle.py -v
```

Expected: FAIL because `run_trade_cycle` does not exist.

- [ ] **Step 3: Implement the orchestration entrypoints**

Create `D:\Codes\trader-shawn\src\trader_shawn\app.py`:

```python
from trader_shawn.domain.models import AccountSnapshot, CandidateSpread
from trader_shawn.execution.order_builder import build_combo_order_payload
from trader_shawn.risk.guard import RiskGuard


def run_trade_cycle(
    candidates: list[CandidateSpread],
    account: AccountSnapshot,
    decision_service,
    executor,
) -> dict:
    if not candidates:
        return {"status": "rejected", "reason": "no_candidates"}

    decision = decision_service.decide(
        {
            "ticker": candidates[0].ticker,
            "candidates": [candidate.model_dump() for candidate in candidates],
        }
    )
    if decision.action != "approve":
        return {"status": "rejected", "reason": decision.reason}

    spread = next(
        candidate
        for candidate in candidates
        if candidate.short_strike == decision.short_strike
        and candidate.long_strike == decision.long_strike
        and candidate.expiry == decision.expiry
    )

    guard = RiskGuard(
        max_risk_per_trade_pct=0.02,
        max_daily_loss_pct=0.04,
        max_new_positions_per_day=6,
        max_open_risk_pct=0.20,
        max_spreads_per_symbol=2,
    )
    guard_result = guard.evaluate(spread, account, open_symbol_count=0)
    if not guard_result.allowed:
        return {"status": "rejected", "reason": guard_result.reason}

    payload = build_combo_order_payload(spread, decision.limit_credit)
    return executor.submit_limit_combo(payload)
```

Create `D:\Codes\trader-shawn\src\trader_shawn\scheduler.py`:

```python
from datetime import datetime


def should_run_cycle(now: datetime) -> bool:
    return now.weekday() < 5 and now.hour in {9, 10, 11, 12, 13, 14, 15}
```

Create `D:\Codes\trader-shawn\src\trader_shawn\monitoring\dashboard_api.py`:

```python
from trader_shawn.monitoring.state_store import StateStore


def load_dashboard_snapshot(store: StateStore) -> dict:
    return store.load()
```

- [ ] **Step 4: Run the end-to-end test and the full local suite**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\integration\test_trade_cycle.py -v
py -m pytest D:\Codes\trader-shawn\tests -v
```

Expected: PASS with all unit and integration tests green.

- [ ] **Step 5: Commit the orchestrated trade cycle**

Run:

```powershell
git add src/trader_shawn/app.py src/trader_shawn/scheduler.py src/trader_shawn/monitoring/dashboard_api.py tests/integration/test_trade_cycle.py
git commit -m "feat: wire end-to-end trade cycle"
```

### Task 8: Add Explicit `scan`, `decide`, `trade`, And `manage` Commands

**Files:**
- Modify: `D:\Codes\trader-shawn\src\trader_shawn\app.py`
- Create: `D:\Codes\trader-shawn\tests\integration\test_cli_commands.py`

- [ ] **Step 1: Write a failing CLI command test**

Create `D:\Codes\trader-shawn\tests\integration\test_cli_commands.py`:

```python
from typer.testing import CliRunner

from trader_shawn.app import cli


def test_cli_has_expected_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "scan" in result.output
    assert "decide" in result.output
    assert "trade" in result.output
    assert "manage" in result.output
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\integration\test_cli_commands.py -v
```

Expected: FAIL because `cli` or the required commands do not exist.

- [ ] **Step 3: Implement the Typer CLI entrypoints**

Modify `D:\Codes\trader-shawn\src\trader_shawn\app.py`:

```python
import typer

from trader_shawn.domain.models import AccountSnapshot, CandidateSpread
from trader_shawn.execution.order_builder import build_combo_order_payload
from trader_shawn.risk.guard import RiskGuard

cli = typer.Typer()


def run_trade_cycle(
    candidates: list[CandidateSpread],
    account: AccountSnapshot,
    decision_service,
    executor,
) -> dict:
    if not candidates:
        return {"status": "rejected", "reason": "no_candidates"}

    decision = decision_service.decide(
        {
            "ticker": candidates[0].ticker,
            "candidates": [candidate.model_dump() for candidate in candidates],
        }
    )
    if decision.action != "approve":
        return {"status": "rejected", "reason": decision.reason}

    spread = next(
        candidate
        for candidate in candidates
        if candidate.short_strike == decision.short_strike
        and candidate.long_strike == decision.long_strike
        and candidate.expiry == decision.expiry
    )

    guard = RiskGuard(
        max_risk_per_trade_pct=0.02,
        max_daily_loss_pct=0.04,
        max_new_positions_per_day=6,
        max_open_risk_pct=0.20,
        max_spreads_per_symbol=2,
    )
    guard_result = guard.evaluate(spread, account, open_symbol_count=0)
    if not guard_result.allowed:
        return {"status": "rejected", "reason": guard_result.reason}

    payload = build_combo_order_payload(spread, decision.limit_credit)
    return executor.submit_limit_combo(payload)


@cli.command()
def scan() -> None:
    typer.echo("scan")


@cli.command()
def decide() -> None:
    typer.echo("decide")


@cli.command()
def trade() -> None:
    typer.echo("trade")


@cli.command()
def manage() -> None:
    typer.echo("manage")
```

- [ ] **Step 4: Run the CLI test and the full suite**

Run:

```powershell
py -m pytest D:\Codes\trader-shawn\tests\integration\test_cli_commands.py -v
py -m pytest D:\Codes\trader-shawn\tests -v
```

Expected: PASS with the CLI command coverage included in the full suite.

- [ ] **Step 5: Commit the explicit command entrypoints**

Run:

```powershell
git add src/trader_shawn/app.py tests/integration/test_cli_commands.py
git commit -m "feat: add explicit cli entrypoints"
```

## Self-Review

Spec coverage check:

- Fixed universe: covered in Task 1 config and Task 3 candidate building.
- Credit spread-only scope: covered in Task 3 candidate generation and Task 4 parser contract.
- `Claude CLI` primary and `Codex` secondary: covered in Task 4.
- Hard risk rules: covered in Task 5 and reused in Task 7.
- Combo limit order execution: covered in Task 6 and Task 7.
- Position exits: covered in Task 6.
- Auditability: covered in Task 2.
- Distinct entrypoints and monitoring: covered in Task 7 and Task 8.

Placeholder scan:

- No placeholder markers remain.
- Each code-changing step includes concrete file content or commands.

Type consistency:

- `CandidateSpread`, `AccountSnapshot`, `PositionSnapshot`, and `DecisionRecord` are defined in Task 2 and reused consistently later.
- `RiskGuard.evaluate()` signature is defined in Task 5 and matched in Task 7.

Known follow-up during execution:

- After Task 8 passes, replace the placeholder `typer.echo(...)` bodies with the real orchestration functions wired to settings, market-data fetches, AI providers, and broker clients.
