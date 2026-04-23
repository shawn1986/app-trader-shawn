# War Room UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a desktop-first war room UI that surfaces broker health, risk, hot positions, anomalies, mission log activity, and armed-gated command controls on one operator-facing screen.

**Architecture:** Add a small `war_room` package that assembles a unified snapshot from existing dashboard and audit data, exposes a FastAPI web app with JSON endpoints plus a static command-center UI, and reuses the current runtime command paths for `scan`, `decide`, `manage`, and `trade`. Keep the UI thin: polling snapshot data, unlocking a session-local armed state, and posting commands back to the backend.

**Tech Stack:** Python 3.12, Click, FastAPI, Uvicorn, Jinja2, vanilla JavaScript, CSS, pytest, Playwright for browser integration tests

---

## File Map

- Create: `src/trader_shawn/war_room/__init__.py`
- Create: `src/trader_shawn/war_room/models.py`
- Create: `src/trader_shawn/war_room/service.py`
- Create: `src/trader_shawn/war_room/commands.py`
- Create: `src/trader_shawn/war_room/web.py`
- Create: `src/trader_shawn/war_room/templates/war_room.html`
- Create: `src/trader_shawn/war_room/static/war_room.css`
- Create: `src/trader_shawn/war_room/static/war_room.js`
- Modify: `src/trader_shawn/monitoring/audit_logger.py`
- Modify: `src/trader_shawn/app.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Test: `tests/unit/test_war_room_service.py`
- Test: `tests/integration/test_war_room_api.py`
- Test: `tests/integration/test_war_room_ui.py`

### Responsibility Notes

- `models.py`: typed UI-facing snapshot structures and helper serialization.
- `service.py`: snapshot assembly, threat scoring, hot-position ranking, mission-log formatting, degradation logic.
- `commands.py`: runtime-backed command bridge plus armed-session store.
- `web.py`: FastAPI app factory, routes, dependency wiring, and static/template mounting.
- `war_room.html`, `war_room.css`, `war_room.js`: the Alpha + Threat Rail UI shell and browser behavior.
- `app.py`: expose a `war-room` CLI command to launch the web app.

### Task 1: Build War Room Snapshot Models And Aggregation Service

**Files:**
- Create: `src/trader_shawn/war_room/__init__.py`
- Create: `src/trader_shawn/war_room/models.py`
- Create: `src/trader_shawn/war_room/service.py`
- Test: `tests/unit/test_war_room_service.py`

- [ ] **Step 1: Write the failing unit tests for threat level, hot positions, and stale broker state**

```python
from datetime import UTC, datetime, timedelta

from trader_shawn.war_room.service import build_war_room_snapshot


def test_build_war_room_snapshot_promotes_manual_intervention_to_critical() -> None:
    snapshot = build_war_room_snapshot(
        dashboard_state={
            "status": "updated",
            "last_cycle": {
                "status": "anomaly",
                "reason": "uncertain_submit_state",
                "manual_intervention_required": True,
                "fingerprints": ["AMD|2026-04-30|P|160.0|155.0|1"],
            },
            "error": None,
        },
        account_snapshot={
            "net_liquidation": 50_000.0,
            "unrealized_pnl": -420.0,
            "open_risk": 4_300.0,
            "new_positions_today": 1,
        },
        managed_positions=[
            {
                "position_id": "pos-1",
                "ticker": "AMD",
                "status": "closing",
                "expiry": "2026-04-30",
                "last_known_debit": 2.25,
                "opened_at": "2026-04-20T09:31:00+00:00",
            }
        ],
        position_events=[
            {
                "position_id": "pos-1",
                "event_type": "close_submit_uncertain",
                "payload_json": {"error": "submit temporarily unavailable"},
                "created_at": "2026-04-21T01:00:00+00:00",
            }
        ],
        broker_health={
            "connected": False,
            "latency_ms": None,
            "checked_at": "2026-04-21T01:02:00+00:00",
            "message": "connection refused",
        },
        now=datetime(2026, 4, 21, 1, 2, tzinfo=UTC),
    )

    assert snapshot["threat_level"] == "critical"
    assert snapshot["command_status"]["broker"]["state"] == "degraded"
    assert snapshot["threat_rail"]["manual_intervention_required"] is True
    assert snapshot["hot_positions"][0]["ticker"] == "AMD"


def test_build_war_room_snapshot_marks_broker_data_stale_after_timeout() -> None:
    snapshot = build_war_room_snapshot(
        dashboard_state={"status": "idle", "last_cycle": {}, "error": None},
        account_snapshot={},
        managed_positions=[],
        position_events=[],
        broker_health={
            "connected": True,
            "latency_ms": 41,
            "checked_at": (datetime(2026, 4, 21, 1, 0, tzinfo=UTC) - timedelta(seconds=45)).isoformat(),
            "message": "",
        },
        now=datetime(2026, 4, 21, 1, 0, tzinfo=UTC),
    )

    assert snapshot["command_status"]["broker"]["freshness"] == "stale"
    assert snapshot["threat_level"] == "warning"
```

- [ ] **Step 2: Run the unit tests to verify they fail**

Run: `py -3.12 -m pytest tests/unit/test_war_room_service.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'trader_shawn.war_room'`

- [ ] **Step 3: Write the minimal models and aggregation service**

Create `src/trader_shawn/war_room/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class BrokerStatus:
    state: str
    freshness: str
    latency_ms: int | None
    checked_at: str | None
    message: str = ""


@dataclass(slots=True)
class ThreatRail:
    level: str
    active_anomalies: int
    manual_intervention_required: bool
    recent_failures: list[dict[str, object]] = field(default_factory=list)


@dataclass(slots=True)
class WarRoomSnapshot:
    generated_at: str
    threat_level: str
    command_status: dict[str, object]
    risk_deck: dict[str, object]
    hot_positions: list[dict[str, object]]
    mission_log: list[dict[str, object]]
    threat_rail: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "threat_level": self.threat_level,
            "command_status": self.command_status,
            "risk_deck": self.risk_deck,
            "hot_positions": self.hot_positions,
            "mission_log": self.mission_log,
            "threat_rail": self.threat_rail,
        }
```

Create `src/trader_shawn/war_room/service.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trader_shawn.war_room.models import WarRoomSnapshot


def build_war_room_snapshot(
    *,
    dashboard_state: dict[str, Any],
    account_snapshot: dict[str, Any],
    managed_positions: list[dict[str, Any]],
    position_events: list[dict[str, Any]],
    broker_health: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    checked_at = _parse_datetime(broker_health.get("checked_at"))
    freshness = "fresh"
    if checked_at is None or (now - checked_at).total_seconds() > 30:
        freshness = "stale"

    active_anomalies = 0
    last_cycle = dashboard_state.get("last_cycle") or {}
    if last_cycle.get("status") == "anomaly":
        active_anomalies += 1

    manual_intervention_required = bool(last_cycle.get("manual_intervention_required"))
    recent_failures = [
        event for event in position_events
        if str(event.get("event_type", "")).endswith("uncertain")
    ][-5:]

    threat_level = "nominal"
    if freshness == "stale" or not broker_health.get("connected", False):
        threat_level = "warning"
    if manual_intervention_required or recent_failures:
        threat_level = "critical"

    hot_positions = sorted(
        managed_positions,
        key=lambda row: (
            0 if str(row.get("status")) in {"closing", "opening"} else 1,
            str(row.get("ticker", "")),
        ),
    )[:5]

    snapshot = WarRoomSnapshot(
        generated_at=now.isoformat(),
        threat_level=threat_level,
        command_status={
            "broker": {
                "state": "ok" if broker_health.get("connected", False) else "degraded",
                "freshness": freshness,
                "latency_ms": broker_health.get("latency_ms"),
                "checked_at": broker_health.get("checked_at"),
                "message": str(broker_health.get("message") or ""),
            },
            "runtime_mode": str(account_snapshot.get("mode", "paper")),
            "armed_state": "monitoring",
            "last_cycle": last_cycle,
        },
        risk_deck={
            "open_risk": float(account_snapshot.get("open_risk", 0.0) or 0.0),
            "unrealized_pnl": float(account_snapshot.get("unrealized_pnl", 0.0) or 0.0),
            "new_positions_today": int(account_snapshot.get("new_positions_today", 0) or 0),
            "active_managed_positions": len(managed_positions),
        },
        hot_positions=hot_positions,
        mission_log=list(recent_failures),
        threat_rail={
            "level": threat_level,
            "active_anomalies": active_anomalies,
            "manual_intervention_required": manual_intervention_required,
            "recent_failures": recent_failures,
        },
    )
    return snapshot.to_dict()


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value)
```

Create `src/trader_shawn/war_room/__init__.py`:

```python
from trader_shawn.war_room.service import build_war_room_snapshot

__all__ = ["build_war_room_snapshot"]
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `py -3.12 -m pytest tests/unit/test_war_room_service.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/trader_shawn/war_room/__init__.py src/trader_shawn/war_room/models.py src/trader_shawn/war_room/service.py tests/unit/test_war_room_service.py
git commit -m "feat: add war room snapshot service"
```

### Task 2: Add FastAPI App Factory And Snapshot Endpoint

**Files:**
- Modify: `pyproject.toml`
- Create: `src/trader_shawn/war_room/web.py`
- Test: `tests/integration/test_war_room_api.py`

- [ ] **Step 1: Write the failing integration test for the snapshot endpoint**

```python
from fastapi.testclient import TestClient

from trader_shawn.war_room.web import create_war_room_app


def test_snapshot_endpoint_returns_war_room_payload() -> None:
    app = create_war_room_app(
        snapshot_provider=lambda: {
            "generated_at": "2026-04-21T01:02:00+00:00",
            "threat_level": "warning",
            "command_status": {"broker": {"state": "ok"}},
            "risk_deck": {"open_risk": 1200.0},
            "hot_positions": [],
            "mission_log": [],
            "threat_rail": {"level": "warning"},
        }
    )
    client = TestClient(app)

    response = client.get("/api/war-room/snapshot")

    assert response.status_code == 200
    assert response.json()["threat_level"] == "warning"
    assert response.json()["risk_deck"]["open_risk"] == 1200.0
```

- [ ] **Step 2: Run the integration test to verify it fails**

Run: `py -3.12 -m pytest tests/integration/test_war_room_api.py -q`

Expected: FAIL with `ModuleNotFoundError` for `fastapi` or `trader_shawn.war_room.web`

- [ ] **Step 3: Add dependencies and create the FastAPI app**

Modify `pyproject.toml`:

```toml
[project]
dependencies = [
    "click>=8.1,<9",
    "ib_insync>=0.9.86,<1",
    "pydantic>=2.7,<3",
    "pyyaml>=6.0.1,<7",
    "fastapi>=0.115,<1",
    "uvicorn>=0.30,<1",
    "jinja2>=3.1,<4",
]
```

Create `src/trader_shawn/war_room/web.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse


def create_war_room_app(
    *,
    snapshot_provider: Callable[[], dict[str, Any]] | None = None,
) -> FastAPI:
    snapshot_provider = snapshot_provider or (lambda: _default_snapshot())
    app = FastAPI(title="Trader Shawn War Room")

    @app.get("/api/war-room/snapshot")
    def get_snapshot() -> JSONResponse:
        return JSONResponse(snapshot_provider())

    return app


def _default_snapshot() -> dict[str, Any]:
    return {
        "generated_at": "",
        "threat_level": "nominal",
        "command_status": {},
        "risk_deck": {},
        "hot_positions": [],
        "mission_log": [],
        "threat_rail": {},
    }
```

- [ ] **Step 4: Run the integration test to verify it passes**

Run: `py -3.12 -m pytest tests/integration/test_war_room_api.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/trader_shawn/war_room/web.py tests/integration/test_war_room_api.py
git commit -m "feat: add war room api shell"
```

### Task 3: Add Armed Session Gate And Runtime-Backed Command Endpoints

**Files:**
- Create: `src/trader_shawn/war_room/commands.py`
- Modify: `src/trader_shawn/war_room/web.py`
- Test: `tests/integration/test_war_room_api.py`

- [ ] **Step 1: Extend the API test with armed-mode and command execution failures**

```python
from fastapi.testclient import TestClient

from trader_shawn.war_room.web import create_war_room_app


def test_command_endpoint_requires_armed_session() -> None:
    app = create_war_room_app(
        snapshot_provider=lambda: {},
        command_runner=lambda command, payload=None: {"status": "ok", "command": command},
    )
    client = TestClient(app)

    response = client.post("/api/war-room/commands/manage", json={})

    assert response.status_code == 403
    assert response.json()["reason"] == "armed_mode_required"


def test_trade_endpoint_requires_explicit_confirmation() -> None:
    app = create_war_room_app(
        snapshot_provider=lambda: {},
        command_runner=lambda command, payload=None: {"status": "submitted", "command": command},
    )
    client = TestClient(app)

    arm = client.post("/api/war-room/arm", json={"phrase": "ARM"})
    assert arm.status_code == 204

    response = client.post("/api/war-room/commands/trade", json={"confirmed": False})

    assert response.status_code == 409
    assert response.json()["reason"] == "trade_confirmation_required"
```

- [ ] **Step 2: Run the API tests to verify they fail**

Run: `py -3.12 -m pytest tests/integration/test_war_room_api.py -q`

Expected: FAIL with `TypeError` because `create_war_room_app()` does not yet accept `command_runner`

- [ ] **Step 3: Implement the armed session store and command bridge**

Create `src/trader_shawn/war_room/commands.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Any

import trader_shawn.app as app_module


class ArmedSessionStore:
    def __init__(self, *, ttl_seconds: int = 900) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._sessions: dict[str, datetime] = {}

    def arm(self) -> str:
        token = token_urlsafe(24)
        self._sessions[token] = datetime.now(UTC) + self._ttl
        return token

    def is_armed(self, token: str | None) -> bool:
        if token is None:
            return False
        expires_at = self._sessions.get(token)
        if expires_at is None or expires_at <= datetime.now(UTC):
            self._sessions.pop(token, None)
            return False
        return True


def run_runtime_command(command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    if command == "scan":
        return app_module._scan_command()
    if command == "decide":
        return app_module._decide_command()
    if command == "manage":
        return app_module._manage_command()
    if command == "trade":
        return app_module._trade_command()
    raise ValueError(f"unsupported war room command: {command}")
```

Modify `src/trader_shawn/war_room/web.py`:

```python
from fastapi import Cookie, FastAPI, Response
from fastapi.responses import JSONResponse

from trader_shawn.war_room.commands import ArmedSessionStore, run_runtime_command


def create_war_room_app(
    *,
    snapshot_provider=None,
    command_runner=None,
) -> FastAPI:
    snapshot_provider = snapshot_provider or (lambda: _default_snapshot())
    command_runner = command_runner or run_runtime_command
    armed_sessions = ArmedSessionStore()
    app = FastAPI(title="Trader Shawn War Room")

    @app.post("/api/war-room/arm", status_code=204)
    def arm_war_room(response: Response, payload: dict[str, str]) -> Response:
        if payload.get("phrase") != "ARM":
            return JSONResponse({"reason": "invalid_arm_phrase"}, status_code=403)
        response.set_cookie("war_room_armed", armed_sessions.arm(), httponly=True, samesite="strict")
        return response

    @app.post("/api/war-room/commands/{command_name}")
    def execute_command(
        command_name: str,
        payload: dict[str, Any],
        war_room_armed: str | None = Cookie(default=None),
    ) -> JSONResponse:
        if not armed_sessions.is_armed(war_room_armed):
            return JSONResponse({"reason": "armed_mode_required"}, status_code=403)
        if command_name == "trade" and payload.get("confirmed") is not True:
            return JSONResponse({"reason": "trade_confirmation_required"}, status_code=409)
        return JSONResponse(command_runner(command_name, payload))
```

- [ ] **Step 4: Run the API tests to verify they pass**

Run: `py -3.12 -m pytest tests/integration/test_war_room_api.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/trader_shawn/war_room/commands.py src/trader_shawn/war_room/web.py tests/integration/test_war_room_api.py
git commit -m "feat: add armed-gated war room commands"
```

### Task 4: Build The Alpha + Threat Rail UI Shell And Launch Command

**Files:**
- Create: `src/trader_shawn/war_room/templates/war_room.html`
- Create: `src/trader_shawn/war_room/static/war_room.css`
- Modify: `src/trader_shawn/war_room/web.py`
- Modify: `src/trader_shawn/app.py`
- Test: `tests/integration/test_war_room_ui.py`

- [ ] **Step 1: Write the failing UI shell test**

```python
from fastapi.testclient import TestClient

from trader_shawn.war_room.web import create_war_room_app


def test_war_room_shell_renders_alpha_layout_copy() -> None:
    client = TestClient(create_war_room_app(snapshot_provider=lambda: {}))

    response = client.get("/war-room")

    assert response.status_code == 200
    html = response.text
    assert "Command Status" in html
    assert "Threat Rail" in html
    assert "Type ARM to unlock" in html
```

- [ ] **Step 2: Run the UI shell test to verify it fails**

Run: `py -3.12 -m pytest tests/integration/test_war_room_ui.py -q`

Expected: FAIL with `404 != 200`

- [ ] **Step 3: Implement the HTML shell, CSS theme, and CLI launch command**

Create `src/trader_shawn/war_room/templates/war_room.html`:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Trader Shawn War Room</title>
    <link rel="stylesheet" href="/static/war_room.css">
  </head>
  <body data-mode="monitoring">
    <main class="war-room">
      <section class="command-status">
        <p class="eyebrow">Command Status</p>
        <h1>Operational Readiness</h1>
      </section>
      <section class="risk-deck">
        <p class="eyebrow">Risk Deck</p>
      </section>
      <section class="hot-positions">
        <p class="eyebrow">Hot Positions</p>
      </section>
      <aside class="threat-rail">
        <p class="eyebrow">Threat Rail</p>
        <div id="threat-level">Nominal</div>
        <div class="armed-gate">Type ARM to unlock</div>
      </aside>
    </main>
    <script src="/static/war_room.js" defer></script>
  </body>
</html>
```

Create `src/trader_shawn/war_room/static/war_room.css`:

```css
:root {
  --bg: #071019;
  --panel: #0d1822;
  --line: rgba(113, 212, 255, 0.18);
  --text: #d8f3ff;
  --warn: #ffb25a;
  --danger: #ff5757;
}

body {
  margin: 0;
  background:
    radial-gradient(circle at top right, rgba(80, 150, 255, 0.12), transparent 28%),
    linear-gradient(180deg, #02060b, var(--bg));
  color: var(--text);
  font-family: "Bahnschrift", "Segoe UI", sans-serif;
}

.war-room {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(300px, 1fr);
  grid-template-areas:
    "command threat"
    "risk threat"
    "positions threat";
  gap: 16px;
  min-height: 100vh;
  padding: 24px;
}

.command-status,
.risk-deck,
.hot-positions,
.threat-rail {
  border: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(13, 24, 34, 0.94), rgba(6, 14, 20, 0.94));
  padding: 18px;
}

.threat-rail {
  grid-area: threat;
  box-shadow: inset 0 0 0 1px rgba(255, 87, 87, 0.14);
}
```

Modify `src/trader_shawn/war_room/web.py`:

```python
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path


def create_war_room_app(*, snapshot_provider=None, command_runner=None) -> FastAPI:
    base_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(base_dir / "templates"))
    app = FastAPI(title="Trader Shawn War Room")
    app.mount("/static", StaticFiles(directory=str(base_dir / "static")), name="static")

    @app.get("/war-room", response_class=HTMLResponse)
    def war_room_page(request):
        return templates.TemplateResponse("war_room.html", {"request": request})
```

Modify `src/trader_shawn/app.py`:

```python
import uvicorn

from trader_shawn.war_room.web import create_war_room_app


@cli.command("war-room")
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8787, type=int)
def war_room_command(host: str, port: int) -> None:
    uvicorn.run(create_war_room_app(), host=host, port=port)
```

- [ ] **Step 4: Run the UI shell test to verify it passes**

Run: `py -3.12 -m pytest tests/integration/test_war_room_ui.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/trader_shawn/war_room/templates/war_room.html src/trader_shawn/war_room/static/war_room.css src/trader_shawn/war_room/web.py src/trader_shawn/app.py tests/integration/test_war_room_ui.py
git commit -m "feat: add war room shell ui"
```

### Task 5: Wire Frontend Polling, Armed UX, And Command Feedback

**Files:**
- Create: `src/trader_shawn/war_room/static/war_room.js`
- Modify: `src/trader_shawn/war_room/templates/war_room.html`
- Modify: `src/trader_shawn/war_room/static/war_room.css`
- Test: `tests/integration/test_war_room_ui.py`

- [ ] **Step 1: Write the failing browser integration test for ARM unlock and threat updates**

```python
import socket
import threading

import pytest
import uvicorn

from playwright.sync_api import sync_playwright

from trader_shawn.war_room.web import create_war_room_app


@pytest.fixture
def live_server():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    app = create_war_room_app(
        snapshot_provider=lambda: {
            "generated_at": "2026-04-21T01:02:00+00:00",
            "threat_level": "warning",
            "command_status": {"broker": {"state": "ok"}},
            "risk_deck": {"open_risk": 1200.0},
            "hot_positions": [],
            "mission_log": [],
            "threat_rail": {"level": "warning"},
        },
        command_runner=lambda command, payload=None: {"status": "ok", "command": command},
    )
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_war_room_unlocks_controls_and_refreshes_threat_level(live_server) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"{live_server}/war-room")

        page.fill('[data-arm-input]', 'ARM')
        page.click('[data-arm-submit]')
        page.wait_for_selector('[data-command="manage"]:not([disabled])')

        expect_text = page.locator('[data-threat-level]').inner_text()
        assert expect_text in {"Warning", "Critical", "Nominal"}
        browser.close()
```

- [ ] **Step 2: Run the browser test to verify it fails**

Run: `py -3.12 -m playwright install chromium`

Run: `py -3.12 -m pytest tests/integration/test_war_room_ui.py -q`

Expected: FAIL because the page does not yet expose arm controls or live threat text bindings

- [ ] **Step 3: Implement polling, ARM unlock, and command posting**

Create `src/trader_shawn/war_room/static/war_room.js`:

```javascript
const state = {
  armed: false,
  pollHandle: null,
  pendingTrade: null,
};

async function fetchSnapshot() {
  const response = await fetch("/api/war-room/snapshot", { credentials: "same-origin" });
  const snapshot = await response.json();
  document.querySelector("[data-threat-level]").textContent = titleCase(snapshot.threat_level);
  document.body.dataset.threat = snapshot.threat_level;
}

async function armWarRoom() {
  const phrase = document.querySelector("[data-arm-input]").value;
  const response = await fetch("/api/war-room/arm", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phrase }),
  });
  if (!response.ok) return;
  state.armed = true;
  document.body.dataset.mode = "armed";
  for (const button of document.querySelectorAll("[data-command]")) {
    button.disabled = false;
  }
}

async function runCommand(commandName) {
  if (commandName === "trade") {
    state.pendingTrade = { command: "trade" };
    document.querySelector("[data-trade-confirm]").hidden = false;
    return;
  }
  const payload = { confirmed: true };
  const response = await fetch(`/api/war-room/commands/${commandName}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  document.querySelector("[data-mission-log]").prepend(renderMissionItem(result));
  await fetchSnapshot();
}

async function confirmTrade() {
  if (!state.pendingTrade) return;
  const response = await fetch("/api/war-room/commands/trade", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed: true }),
  });
  const result = await response.json();
  document.querySelector("[data-mission-log]").prepend(renderMissionItem(result));
  document.querySelector("[data-trade-confirm]").hidden = true;
  state.pendingTrade = null;
  await fetchSnapshot();
}

function titleCase(value) {
  return String(value || "").replace(/^./, (char) => char.toUpperCase());
}

function renderMissionItem(result) {
  const item = document.createElement("li");
  item.textContent = `${result.command || "command"}: ${result.status || "unknown"}`;
  return item;
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelector("[data-arm-submit]").addEventListener("click", armWarRoom);
  document.querySelector("[data-trade-confirm-submit]").addEventListener("click", confirmTrade);
  for (const button of document.querySelectorAll("[data-command]")) {
    button.addEventListener("click", () => runCommand(button.dataset.command));
  }
  fetchSnapshot();
  state.pollHandle = window.setInterval(fetchSnapshot, 5000);
});
```

Modify `src/trader_shawn/war_room/templates/war_room.html` to add the required bindings:

```html
<aside class="threat-rail">
  <p class="eyebrow">Threat Rail</p>
  <div id="threat-level" data-threat-level>Nominal</div>
  <label class="armed-gate">
    <span>Type ARM to unlock</span>
    <input data-arm-input type="password" autocomplete="off">
    <button data-arm-submit type="button">Arm</button>
  </label>
  <ul class="mission-log" data-mission-log></ul>
  <div class="controls">
    <button data-command="scan" disabled>scan</button>
    <button data-command="decide" disabled>decide</button>
    <button data-command="manage" disabled>manage</button>
    <button data-command="trade" disabled>trade</button>
  </div>
  <section class="trade-confirm" data-trade-confirm hidden>
    <p>Trade confirmation required</p>
    <p>Review mode, ticker, strategy, limit credit, and risk status before firing.</p>
    <button data-trade-confirm-submit type="button">Confirm trade</button>
  </section>
</aside>
```

- [ ] **Step 4: Run the browser test to verify it passes**

Run: `py -3.12 -m pytest tests/integration/test_war_room_ui.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/trader_shawn/war_room/static/war_room.js src/trader_shawn/war_room/templates/war_room.html src/trader_shawn/war_room/static/war_room.css tests/integration/test_war_room_ui.py
git commit -m "feat: wire war room interactions"
```

### Task 6: Connect Real Runtime Data Sources And Document Operator Startup

**Files:**
- Modify: `src/trader_shawn/war_room/service.py`
- Modify: `src/trader_shawn/war_room/web.py`
- Modify: `src/trader_shawn/monitoring/audit_logger.py`
- Modify: `README.md`
- Test: `tests/unit/test_war_room_service.py`
- Test: `tests/integration/test_war_room_api.py`

- [ ] **Step 1: Write failing tests for real snapshot assembly from dashboard and audit data**

```python
from pathlib import Path

from trader_shawn.monitoring.audit_logger import AuditLogger
from trader_shawn.war_room.web import create_default_snapshot_provider


def test_default_snapshot_provider_reads_dashboard_and_audit_db(tmp_path: Path) -> None:
    dashboard_path = tmp_path / "dashboard.json"
    dashboard_path.write_text(
        '{"status":"updated","last_cycle":{"status":"ok","managed_count":2},"error":null}',
        encoding="utf-8",
    )
    audit_logger = AuditLogger(tmp_path / "audit.db")
    audit_logger.upsert_managed_position(
        {
            "position_id": "pos-1",
            "ticker": "AMD",
            "strategy": "bull_put_credit_spread",
            "expiry": "2026-04-30",
            "short_strike": 160.0,
            "long_strike": 155.0,
            "quantity": 1,
            "entry_credit": 1.1,
            "mode": "paper",
            "status": "open",
            "opened_at": "2026-04-20T09:31:00+00:00",
            "broker_fingerprint": "AMD|2026-04-30|P|160.0|155.0|1",
        }
    )
    audit_logger.record_position_event(
        "pos-1",
        "close_submit_uncertain",
        {"error": "submit temporarily unavailable"},
        created_at="2026-04-21T01:00:00+00:00",
    )

    provider = create_default_snapshot_provider(
        dashboard_state_path=dashboard_path,
        audit_db_path=tmp_path / "audit.db",
    )

    snapshot = provider()

    assert snapshot["risk_deck"]["active_managed_positions"] == 1
    assert snapshot["command_status"]["last_cycle"]["status"] == "ok"
    assert snapshot["mission_log"][0]["event_type"] == "close_submit_uncertain"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3.12 -m pytest tests/unit/test_war_room_service.py tests/integration/test_war_room_api.py -q`

Expected: FAIL because `create_default_snapshot_provider()` does not exist yet

- [ ] **Step 3: Implement default snapshot provider and document startup**

Modify `src/trader_shawn/monitoring/audit_logger.py`:

```python
    def fetch_recent_position_events(self, *, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select position_id, event_type, payload_json, created_at
                from position_events
                order by created_at desc, id desc
                limit ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            {
                "position_id": row["position_id"],
                "event_type": row["event_type"],
                "payload_json": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
```

Modify `src/trader_shawn/war_room/web.py`:

```python
from trader_shawn.app import build_cli_runtime
from trader_shawn.monitoring.dashboard_api import read_dashboard_snapshot
from trader_shawn.monitoring.audit_logger import AuditLogger
from trader_shawn.war_room.service import build_war_room_snapshot


def create_default_snapshot_provider(*, dashboard_state_path: Path | None = None, audit_db_path: Path | None = None):
    runtime = build_cli_runtime()
    dashboard_state_path = dashboard_state_path or runtime.dashboard_state_path
    audit_db_path = audit_db_path or runtime.settings.audit_db_path

    def provider() -> dict[str, object]:
        dashboard_state = read_dashboard_snapshot(dashboard_state_path)
        audit_logger = AuditLogger(Path(audit_db_path))
        managed_positions = audit_logger.fetch_active_managed_positions(mode=runtime.settings.mode)
        position_events = audit_logger.fetch_recent_position_events(limit=10)
        broker_health = _probe_broker_health(runtime.account_service)
        return build_war_room_snapshot(
            dashboard_state=dashboard_state,
            account_snapshot={"mode": runtime.settings.mode},
            managed_positions=managed_positions,
            position_events=position_events,
            broker_health=broker_health,
        )

    return provider


def _probe_broker_health(account_service) -> dict[str, object]:
    started_at = datetime.now(UTC)
    try:
        account_service.fetch_account_snapshot()
    except Exception as exc:
        return {
            "connected": False,
            "latency_ms": None,
            "checked_at": started_at.isoformat(),
            "message": str(exc),
        }
    latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
    return {
        "connected": True,
        "latency_ms": latency_ms,
        "checked_at": started_at.isoformat(),
        "message": "",
    }
```

Modify `README.md`:

```md
## War Room

Start the command-center UI locally:

`$env:PYTHONPATH='src'; py -3.12 -m trader_shawn.app war-room --host 127.0.0.1 --port 8787`

Then open `http://127.0.0.1:8787/war-room`.

The UI defaults to monitoring mode. Type `ARM` in the right-side gate to unlock `scan`, `decide`, `manage`, and `trade` controls for the current browser session.
```

- [ ] **Step 4: Run focused tests, then the full suite**

Run: `py -3.12 -m pytest tests/unit/test_war_room_service.py tests/integration/test_war_room_api.py tests/integration/test_war_room_ui.py -q`

Expected: PASS

Run: `py -3.12 -m pytest -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/trader_shawn/war_room/service.py src/trader_shawn/war_room/web.py src/trader_shawn/monitoring/audit_logger.py README.md tests/unit/test_war_room_service.py tests/integration/test_war_room_api.py
git commit -m "feat: connect war room to runtime data"
```
