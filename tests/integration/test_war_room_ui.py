import socket
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import Error as PlaywrightError, sync_playwright

from trader_shawn.war_room.web import create_war_room_app


@pytest.fixture
def live_server():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    threat_state = {"value": "warning"}

    def snapshot_provider() -> dict[str, object]:
        return {
            "generated_at": "2026-04-21T01:02:00+00:00",
            "threat_level": threat_state["value"],
            "command_status": {"broker": {"state": "ok"}},
            "risk_deck": {"open_risk": 1200.0},
            "hot_positions": [],
            "mission_log": [],
            "threat_rail": {"level": threat_state["value"]},
        }

    def command_runner(
        command: str,
        payload: dict[str, object] | None = None,
        *,
        progress_callback=None,
    ) -> dict[str, str]:
        _ = payload
        if command == "scan":
            if callable(progress_callback):
                progress_callback(
                    {
                        "stage": "scan_symbol_fetching",
                        "message": "Fetching SPY option quotes.",
                        "symbol": "SPY",
                        "current": 0,
                        "total": 3,
                        "unit": "symbols",
                    }
                )
            time.sleep(0.2)
            if callable(progress_callback):
                progress_callback(
                    {
                        "stage": "scan_symbol_completed",
                        "message": "SPY complete. 0 quotes, 0 candidates, 1 watchlist.",
                        "symbol": "SPY",
                        "current": 1,
                        "total": 3,
                        "unit": "symbols",
                    }
                )
                progress_callback(
                    {
                        "stage": "scan_symbol_fetching",
                        "message": "Fetching QQQ option quotes.",
                        "symbol": "QQQ",
                        "current": 1,
                        "total": 3,
                        "unit": "symbols",
                    }
                )
            time.sleep(0.2)
        if command == "manage":
            threat_state["value"] = "critical"
        if command == "trade":
            threat_state["value"] = "nominal"
        return {"status": "ok", "command": command}

    app = create_war_room_app(snapshot_provider=snapshot_provider, command_runner=command_runner)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{host}:{port}"

    ready = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/war-room", timeout=0.2) as response:
                if response.status == 200:
                    ready = True
                    break
        except URLError:
            time.sleep(0.05)

    if not ready:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("live_server did not become ready within 5 seconds")

    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _launch_chromium_or_skip(playwright):
    executable_path = Path(playwright.chromium.executable_path)
    if not executable_path.exists():
        pytest.skip(f"Playwright Chromium executable is unavailable at {executable_path}")

    try:
        return playwright.chromium.launch()
    except PlaywrightError as exc:
        pytest.skip(f"Playwright Chromium is unavailable: {exc}")


def test_war_room_shell_renders_alpha_layout_copy() -> None:
    client = TestClient(create_war_room_app(snapshot_provider=lambda: {}))

    response = client.get("/war-room")

    assert response.status_code == 200
    html = response.text
    assert "Workflow" in html
    assert "Threat Rail" in html
    assert "Type ARM to unlock" in html
    assert "data-primary-command" in html
    assert "data-arm-submit" not in html
    assert 'data-command="scan"' not in html
    assert 'data-command="decide"' not in html
    assert 'data-command="trade"' not in html


def test_war_room_static_assets_are_available() -> None:
    client = TestClient(create_war_room_app(snapshot_provider=lambda: {}))

    css_response = client.get("/static/war_room.css")
    js_response = client.get("/static/war_room.js")

    assert css_response.status_code == 200
    assert js_response.status_code == 200
    assert "scrollbar-width: thin;" in css_response.text
    assert ".command-overlay__event-log::-webkit-scrollbar-button" in css_response.text
    assert "[hidden]" in css_response.text
    assert "display: none !important;" in css_response.text


def test_war_room_static_asset_throttles_command_status_and_busy_snapshot_polling() -> None:
    client = TestClient(create_war_room_app(snapshot_provider=lambda: {}))

    response = client.get("/static/war_room.js")

    assert response.status_code == 200
    source = response.text
    assert "const COMMAND_STATUS_POLL_MS = 1500;" in source
    assert "if (busyCommand !== null) {" in source
    assert "return null;" in source
    assert "if (activeCommandJobId !== null) {" in source


def test_war_room_primary_cta_unlocks_before_scan(live_server) -> None:
    with sync_playwright() as p:
        browser = _launch_chromium_or_skip(p)
        page = browser.new_page()
        page.goto(f"{live_server}/war-room")

        page.wait_for_function(
            "() => document.querySelector('[data-primary-command]')?.textContent.includes('Unlock War Room')"
        )
        page.wait_for_selector("[data-primary-command]:not([disabled])")

        page.click("[data-primary-command]")
        page.wait_for_function(
            "() => document.activeElement === document.querySelector('[data-arm-input]')"
        )
        page.wait_for_function(
            "() => document.querySelector('[data-primary-tip]')?.textContent.includes('Type ARM in the authorization phrase field')"
        )

        page.fill("[data-arm-input]", "ARM")
        page.click("[data-primary-command]")
        page.wait_for_function("() => document.body.dataset.mode === 'armed'")
        page.wait_for_function(
            "() => document.querySelector('[data-primary-command]')?.textContent.includes('Run Scan')"
        )

        browser.close()


def test_war_room_unlocks_controls_and_refreshes_threat_level(live_server) -> None:
    with sync_playwright() as p:
        browser = _launch_chromium_or_skip(p)
        page = browser.new_page()
        page.goto(f"{live_server}/war-room")
        page.wait_for_function(
            "() => document.querySelector('[data-threat-level]')?.textContent.trim() === 'Warning'"
        )
        page.wait_for_function(
            "() => getComputedStyle(document.querySelector('[data-trade-confirm]')).display === 'none'"
        )

        page.fill("[data-arm-input]", "ARM")
        page.click("[data-primary-command]")
        page.wait_for_selector('[data-secondary-command="manage"]:not([disabled])')

        page.click('[data-secondary-command="manage"]')
        page.wait_for_function(
            "() => document.querySelector('[data-mission-log] li')?.textContent.includes('MANAGE ok')"
        )
        page.wait_for_function(
            "() => document.querySelector('[data-threat-level]')?.textContent.trim() === 'Critical'"
        )
        page.wait_for_function(
            "() => document.querySelector('[data-trade-confirm]')?.hidden === true"
        )

        browser.close()


def test_war_room_relocks_after_armed_session_expires(live_server) -> None:
    with sync_playwright() as p:
        browser = _launch_chromium_or_skip(p)
        page = browser.new_page()
        page.goto(f"{live_server}/war-room")
        page.wait_for_function(
            "() => document.querySelector('[data-threat-level]')?.textContent.trim() === 'Warning'"
        )

        page.fill("[data-arm-input]", "ARM")
        page.click("[data-primary-command]")
        page.wait_for_selector('[data-secondary-command="manage"]:not([disabled])')
        page.wait_for_selector('[data-primary-command]:not([disabled])')

        page.context.clear_cookies()

        page.click('[data-secondary-command="manage"]')

        page.wait_for_function("() => document.body.dataset.mode === 'monitoring'")
        page.wait_for_selector('[data-secondary-command="manage"][disabled]')
        page.wait_for_function(
            "() => document.querySelector('[data-trade-confirm]')?.hidden === true"
        )
        page.wait_for_function(
            "() => document.querySelector('[data-mission-log] li')?.textContent.includes('armed_mode_required')"
        )

        browser.close()


def test_war_room_shows_full_overlay_while_command_is_running(live_server) -> None:
    with sync_playwright() as p:
        browser = _launch_chromium_or_skip(p)
        page = browser.new_page()
        page.goto(f"{live_server}/war-room")
        page.fill("[data-arm-input]", "ARM")
        page.click("[data-primary-command]")
        page.wait_for_selector('[data-primary-command]:not([disabled])')

        page.click('[data-primary-command]')
        page.wait_for_selector("[data-command-overlay]:not([hidden])")
        page.wait_for_function(
            "() => document.querySelector('[data-overlay-command]')?.textContent.includes('SCAN')"
        )
        page.wait_for_function(
            "() => document.querySelector('[data-overlay-progress-label]')?.textContent.includes('symbols')"
        )
        page.wait_for_function(
            "() => document.querySelector('[data-overlay-events]')?.textContent.includes('Fetching SPY option quotes')"
        )
        page.wait_for_selector('[data-secondary-command="manage"][disabled]')

        page.wait_for_function(
            "() => document.querySelector('[data-command-overlay]')?.hidden === true"
        )
        page.wait_for_function(
            "() => document.querySelector('[data-mission-log] li')?.textContent.includes('SCAN ok')"
        )

        browser.close()


def test_war_room_surfaces_scan_symbol_errors_after_overlay_closes() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    def snapshot_provider() -> dict[str, object]:
        return {
            "generated_at": "2026-04-21T01:02:00+00:00",
            "threat_level": "warning",
            "command_status": {"broker": {"state": "ok"}},
            "risk_deck": {"open_risk": 1200.0},
            "hot_positions": [],
            "mission_log": [],
            "threat_rail": {"level": "warning"},
        }

    def command_runner(
        command: str,
        payload: dict[str, object] | None = None,
        *,
        progress_callback=None,
    ) -> dict[str, object]:
        _ = payload
        assert command == "scan"
        if callable(progress_callback):
            progress_callback(
                {
                    "stage": "scan_symbol_failed",
                    "message": "SPY failed: IBKR request timed out",
                    "symbol": "SPY",
                    "current": 1,
                    "total": 5,
                    "unit": "symbols",
                }
            )
        return {
            "status": "ok",
            "command": "scan",
            "candidate_count": 0,
            "candidates": [],
            "symbol_error_count": 5,
            "symbol_errors": [
                {
                    "symbol": "SPY",
                    "error_type": "TimeoutError",
                    "message": "IBKR request timed out",
                }
            ],
        }

    app = create_war_room_app(snapshot_provider=snapshot_provider, command_runner=command_runner)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{host}:{port}"

    ready = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/war-room", timeout=0.2) as response:
                if response.status == 200:
                    ready = True
                    break
        except URLError:
            time.sleep(0.05)

    if not ready:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("live_server did not become ready within 5 seconds")

    try:
        with sync_playwright() as p:
            browser = _launch_chromium_or_skip(p)
            page = browser.new_page()
            page.goto(f"{base_url}/war-room")
            page.fill("[data-arm-input]", "ARM")
            page.click("[data-primary-command]")
            page.wait_for_selector('[data-primary-command]:not([disabled])')

            page.click('[data-primary-command]')
            page.wait_for_function(
                "() => document.querySelector('[data-command-overlay]')?.hidden === true"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-mission-log] li')?.dataset.severity === 'warning'"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-mission-log] li')?.textContent.includes('SCAN warning')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-mission-log] li')?.textContent.includes('5 symbol errors')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-command-copy]')?.textContent.includes('5 symbol errors')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-threat-copy]')?.textContent.includes('SPY: IBKR request timed out')"
            )
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_war_room_surfaces_successful_scan_counts_after_overlay_closes() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    def snapshot_provider() -> dict[str, object]:
        return {
            "generated_at": "2026-04-21T01:02:00+00:00",
            "threat_level": "nominal",
            "command_status": {"broker": {"state": "ok"}},
            "risk_deck": {"open_risk": 1200.0},
            "hot_positions": [],
            "mission_log": [],
            "threat_rail": {"level": "nominal"},
        }

    def command_runner(command: str, payload=None, *, progress_callback=None) -> dict[str, object]:
        _ = payload
        _ = progress_callback
        assert command == "scan"
        return {
            "status": "ok",
            "command": "scan",
            "candidate_count": 0,
            "candidates": [],
            "watchlist_count": 0,
            "symbol_summaries": [
                {
                    "symbol": "AMD",
                    "quotes_count": 8,
                    "candidate_count": 0,
                    "watchlist_count": 0,
                }
            ],
        }

    app = create_war_room_app(snapshot_provider=snapshot_provider, command_runner=command_runner)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{host}:{port}"

    ready = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/war-room", timeout=0.2) as response:
                if response.status == 200:
                    ready = True
                    break
        except URLError:
            time.sleep(0.05)

    if not ready:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("live_server did not become ready within 5 seconds")

    try:
        with sync_playwright() as p:
            browser = _launch_chromium_or_skip(p)
            page = browser.new_page()
            page.goto(f"{base_url}/war-room")
            page.fill("[data-arm-input]", "ARM")
            page.click("[data-primary-command]")
            page.wait_for_selector('[data-primary-command]:not([disabled])')
            page.wait_for_function(
                "() => document.querySelector('[data-primary-command]')?.textContent.includes('Run Scan')"
            )

            page.click('[data-primary-command]')
            page.wait_for_function(
                "() => document.querySelector('[data-command-overlay]')?.hidden === true"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-trade-confirm]')?.hidden === true"
            )
            page.wait_for_function(
                "() => !document.querySelector('[data-primary-command]')?.textContent.includes('Stage Trade')"
            )
            page.wait_for_selector("[data-next-actions]:not([hidden])")
            page.wait_for_function(
                "() => document.querySelector('[data-next-actions]')?.textContent.includes('No tradable candidates')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-next-actions]')?.textContent.includes('Review watchlist observations')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-next-actions]')?.textContent.includes('Relax filters or widen scan inputs')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-next-actions]')?.textContent.includes('Run Scan again')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-mission-log] li')?.dataset.severity === 'ok'"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-mission-log] li')?.textContent.includes('SCAN ok')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-mission-log] li')?.textContent.includes('0 candidates')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-mission-log] li')?.textContent.includes('AMD 8 quotes')"
            )
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_war_room_enables_trade_only_after_approved_decision() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    def snapshot_provider() -> dict[str, object]:
        return {
            "generated_at": "2026-04-21T01:02:00+00:00",
            "threat_level": "nominal",
            "command_status": {"broker": {"state": "ok"}},
            "risk_deck": {"open_risk": 1200.0},
            "hot_positions": [],
            "mission_log": [],
            "threat_rail": {"level": "nominal"},
        }

    def command_runner(command: str, payload=None, *, progress_callback=None) -> dict[str, object]:
        _ = payload
        _ = progress_callback
        if command == "scan":
            return {
                "status": "ok",
                "command": "scan",
                "candidate_count": 1,
                "candidates": [{"ticker": "AMD"}],
            }
        assert command == "decide"
        return {
            "status": "ok",
            "command": "decide",
            "candidate_count": 1,
            "candidates": [{"ticker": "AMD"}],
            "decision": {"action": "approve", "ticker": "AMD", "limit_credit": 1.25},
        }

    app = create_war_room_app(snapshot_provider=snapshot_provider, command_runner=command_runner)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{host}:{port}"

    ready = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/war-room", timeout=0.2) as response:
                if response.status == 200:
                    ready = True
                    break
        except URLError:
            time.sleep(0.05)

    if not ready:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("live_server did not become ready within 5 seconds")

    try:
        with sync_playwright() as p:
            browser = _launch_chromium_or_skip(p)
            page = browser.new_page()
            page.goto(f"{base_url}/war-room")
            page.fill("[data-arm-input]", "ARM")
            page.click("[data-primary-command]")
            page.wait_for_selector('[data-primary-command]:not([disabled])')
            page.wait_for_function(
                "() => document.querySelector('[data-primary-command]')?.textContent.includes('Run Scan')"
            )

            page.click('[data-primary-command]')
            page.wait_for_function(
                "() => document.querySelector('[data-command-overlay]')?.hidden === true"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-primary-command]')?.textContent.includes('Run Decide')"
            )
            page.click('[data-primary-command]')
            page.wait_for_function(
                "() => document.querySelector('[data-command-overlay]')?.hidden === true"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-primary-command]')?.textContent.includes('Stage Trade')"
            )
            page.wait_for_selector("[data-next-actions]:not([hidden])")
            page.wait_for_function(
                "() => document.querySelector('[data-next-actions]')?.textContent.includes('Trade ready')"
            )

            page.click('[data-primary-command]')
            page.wait_for_selector("[data-trade-confirm]:not([hidden])")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_war_room_shows_candidate_preview_after_scan_finds_candidates() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    def snapshot_provider() -> dict[str, object]:
        return {
            "generated_at": "2026-04-21T01:02:00+00:00",
            "threat_level": "nominal",
            "command_status": {"broker": {"state": "ok"}},
            "risk_deck": {"open_risk": 1200.0},
            "hot_positions": [],
            "mission_log": [],
            "threat_rail": {"level": "nominal"},
        }

    def command_runner(command: str, payload=None, *, progress_callback=None) -> dict[str, object]:
        _ = payload
        _ = progress_callback
        assert command == "scan"
        return {
            "status": "ok",
            "command": "scan",
            "candidate_count": 5,
            "watchlist_count": 4,
            "candidates": [
                {
                    "ticker": "AMD",
                    "strategy": "bull_put_credit_spread",
                    "expiry": "2026-05-08",
                    "dte": 13,
                    "short_strike": 295.0,
                    "long_strike": 290.0,
                    "credit": 0.75,
                    "max_loss": 4.25,
                    "short_delta": 0.1218,
                    "pop": 0.8782,
                    "bid_ask_ratio": 0.6667,
                    "width": 5.0,
                },
                {
                    "ticker": "AMD",
                    "strategy": "bull_put_credit_spread",
                    "expiry": "2026-05-08",
                    "dte": 13,
                    "short_strike": 292.5,
                    "long_strike": 287.5,
                    "credit": 0.61,
                    "max_loss": 4.39,
                    "short_delta": 0.1131,
                    "pop": 0.8869,
                    "bid_ask_ratio": 0.6885,
                    "width": 5.0,
                },
            ],
            "symbol_summaries": [
                {
                    "symbol": "AMD",
                    "quotes_count": 8,
                    "candidate_count": 5,
                    "watchlist_count": 0,
                }
            ],
        }

    app = create_war_room_app(snapshot_provider=snapshot_provider, command_runner=command_runner)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{host}:{port}"

    ready = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/war-room", timeout=0.2) as response:
                if response.status == 200:
                    ready = True
                    break
        except URLError:
            time.sleep(0.05)

    if not ready:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("live_server did not become ready within 5 seconds")

    try:
        with sync_playwright() as p:
            browser = _launch_chromium_or_skip(p)
            page = browser.new_page()
            page.goto(f"{base_url}/war-room")
            page.fill("[data-arm-input]", "ARM")
            page.click("[data-primary-command]")
            page.wait_for_selector('[data-primary-command]:not([disabled])')

            page.click('[data-primary-command]')
            page.wait_for_function(
                "() => document.querySelector('[data-command-overlay]')?.hidden === true"
            )
            page.wait_for_selector("[data-candidate-preview]:not([hidden])")
            page.wait_for_function(
                "() => document.querySelector('[data-candidate-preview]')?.textContent.includes('AMD')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-candidate-preview]')?.textContent.includes('295 / 290')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-candidate-preview]')?.textContent.includes('Credit 0.75')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-candidate-preview]')?.textContent.includes('Max loss 4.25')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-candidate-preview]')?.textContent.includes('Delta 0.12')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-candidate-preview]')?.textContent.includes('POP 88%')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-primary-command]')?.textContent.includes('Run Decide')"
            )
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_war_room_surfaces_decide_provider_failure_detail() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    def snapshot_provider() -> dict[str, object]:
        return {
            "generated_at": "2026-04-21T01:02:00+00:00",
            "threat_level": "nominal",
            "command_status": {"broker": {"state": "ok"}},
            "risk_deck": {"open_risk": 1200.0},
            "hot_positions": [],
            "mission_log": [],
            "threat_rail": {"level": "nominal"},
        }

    def command_runner(command: str, payload=None, *, progress_callback=None) -> dict[str, object]:
        _ = payload
        _ = progress_callback
        if command == "scan":
            return {
                "status": "ok",
                "command": "scan",
                "candidate_count": 1,
                "candidates": [{"ticker": "AMD"}],
            }
        assert command == "decide"
        return {
            "status": "decision_error",
            "command": "decide",
            "reason": "decision_service_failed",
            "error_type": "AiProviderError",
            "message": "claude: provider command failed with exit code 1",
        }

    app = create_war_room_app(snapshot_provider=snapshot_provider, command_runner=command_runner)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{host}:{port}"

    ready = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/war-room", timeout=0.2) as response:
                if response.status == 200:
                    ready = True
                    break
        except URLError:
            time.sleep(0.05)

    if not ready:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("live_server did not become ready within 5 seconds")

    try:
        with sync_playwright() as p:
            browser = _launch_chromium_or_skip(p)
            page = browser.new_page()
            page.goto(f"{base_url}/war-room")
            page.fill("[data-arm-input]", "ARM")
            page.click("[data-primary-command]")
            page.wait_for_selector('[data-primary-command]:not([disabled])')

            page.click('[data-primary-command]')
            page.wait_for_function(
                "() => document.querySelector('[data-command-overlay]')?.hidden === true"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-primary-command]')?.textContent.includes('Run Decide')"
            )
            page.click('[data-primary-command]')
            page.wait_for_function(
                "() => document.querySelector('[data-command-overlay]')?.hidden === true"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-command-copy]')?.textContent.includes('claude: provider command failed with exit code 1')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-threat-copy]')?.textContent.includes('AiProviderError')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-mission-log] li')?.textContent.includes('decision_service_failed')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-mission-log] li')?.textContent.includes('claude: provider command failed with exit code 1')"
            )
            page.wait_for_selector("[data-next-actions]:not([hidden])")
            page.wait_for_function(
                "() => document.querySelector('[data-next-actions]')?.textContent.includes('Decision provider failed')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-next-actions]')?.textContent.includes('Check the Claude CLI session')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-next-actions]')?.textContent.includes('Increase provider_timeout_seconds')"
            )
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_war_room_surfaces_decide_rejection_reason() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    def snapshot_provider() -> dict[str, object]:
        return {
            "generated_at": "2026-04-21T01:02:00+00:00",
            "threat_level": "nominal",
            "command_status": {"broker": {"state": "ok"}},
            "risk_deck": {"open_risk": 1200.0},
            "hot_positions": [],
            "mission_log": [],
            "threat_rail": {"level": "nominal"},
        }

    def command_runner(command: str, payload=None, *, progress_callback=None) -> dict[str, object]:
        _ = payload
        _ = progress_callback
        if command == "scan":
            return {
                "status": "ok",
                "command": "scan",
                "candidate_count": 1,
                "candidates": [{"ticker": "AMD"}],
            }
        assert command == "decide"
        return {
            "status": "ok",
            "command": "decide",
            "candidate_count": 5,
            "decision": {
                "action": "reject",
                "reason": "Bid-ask spread too wide and zero open interest.",
            },
        }

    app = create_war_room_app(snapshot_provider=snapshot_provider, command_runner=command_runner)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{host}:{port}"

    ready = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/war-room", timeout=0.2) as response:
                if response.status == 200:
                    ready = True
                    break
        except URLError:
            time.sleep(0.05)

    if not ready:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("live_server did not become ready within 5 seconds")

    try:
        with sync_playwright() as p:
            browser = _launch_chromium_or_skip(p)
            page = browser.new_page()
            page.goto(f"{base_url}/war-room")
            page.fill("[data-arm-input]", "ARM")
            page.click("[data-primary-command]")
            page.wait_for_selector('[data-primary-command]:not([disabled])')

            page.click('[data-primary-command]')
            page.wait_for_function(
                "() => document.querySelector('[data-command-overlay]')?.hidden === true"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-primary-command]')?.textContent.includes('Run Decide')"
            )
            page.click('[data-primary-command]')
            page.wait_for_function(
                "() => document.querySelector('[data-command-overlay]')?.hidden === true"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-mission-log] li')?.textContent.includes('DECIDE reject')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-command-copy]')?.textContent.includes('Bid-ask spread too wide')"
            )
            page.wait_for_function(
                "() => !document.querySelector('[data-primary-command]')?.textContent.includes('Stage Trade')"
            )
            page.wait_for_selector("[data-next-actions]:not([hidden])")
            page.wait_for_function(
                "() => document.querySelector('[data-next-actions]')?.textContent.includes('Decision rejected')"
            )
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_war_room_attaches_overlay_to_existing_running_command_after_conflict() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    release_scan = threading.Event()
    scan_entered = threading.Event()

    def snapshot_provider() -> dict[str, object]:
        return {
            "generated_at": "2026-04-21T01:02:00+00:00",
            "threat_level": "warning",
            "command_status": {"broker": {"state": "ok"}},
            "risk_deck": {"open_risk": 1200.0},
            "hot_positions": [],
            "mission_log": [],
            "threat_rail": {"level": "warning"},
        }

    def command_runner(
        command: str,
        payload: dict[str, object] | None = None,
        *,
        progress_callback=None,
    ) -> dict[str, str]:
        _ = payload
        assert command == "scan"
        if callable(progress_callback):
            progress_callback(
                {
                    "stage": "scan_symbol_fetching",
                    "message": "Fetching SPY option quotes.",
                    "symbol": "SPY",
                    "current": 0,
                    "total": 1,
                    "unit": "symbols",
                }
            )
        scan_entered.set()
        assert release_scan.wait(timeout=5.0)
        return {"status": "ok", "command": command}

    app = create_war_room_app(snapshot_provider=snapshot_provider, command_runner=command_runner)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{host}:{port}"

    ready = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/war-room", timeout=0.2) as response:
                if response.status == 200:
                    ready = True
                    break
        except URLError:
            time.sleep(0.05)

    if not ready:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("live_server did not become ready within 5 seconds")

    try:
        with sync_playwright() as p:
            browser = _launch_chromium_or_skip(p)
            context = browser.new_context()
            first_page = context.new_page()
            first_page.goto(f"{base_url}/war-room")
            first_page.fill("[data-arm-input]", "ARM")
            first_page.click("[data-primary-command]")
            first_page.wait_for_selector('[data-primary-command]:not([disabled])')
            first_page.click('[data-primary-command]')
            assert scan_entered.wait(timeout=3.0)

            second_page = context.new_page()
            second_page.goto(f"{base_url}/war-room")

            second_page.wait_for_selector("[data-command-overlay]:not([hidden])")
            second_page.wait_for_function(
                "() => document.querySelector('[data-overlay-command]')?.textContent.includes('SCAN')"
            )
            second_page.wait_for_function(
                "() => document.querySelector('[data-overlay-events]')?.textContent.includes('Fetching SPY option quotes')"
            )

            release_scan.set()
            second_page.wait_for_function(
                "() => document.querySelector('[data-command-overlay]')?.hidden === true"
            )
            second_page.wait_for_function(
                "() => document.querySelector('[data-mission-log] li')?.textContent.includes('SCAN ok')"
            )
            browser.close()
    finally:
        release_scan.set()
        server.should_exit = True
        thread.join(timeout=5)


def test_war_room_restores_overlay_when_command_is_already_running() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    release_scan = threading.Event()
    scan_entered = threading.Event()

    def snapshot_provider() -> dict[str, object]:
        return {
            "generated_at": "2026-04-21T01:02:00+00:00",
            "threat_level": "warning",
            "command_status": {"broker": {"state": "ok"}},
            "risk_deck": {"open_risk": 1200.0},
            "hot_positions": [],
            "mission_log": [],
            "threat_rail": {"level": "warning"},
        }

    def command_runner(
        command: str,
        payload: dict[str, object] | None = None,
        *,
        progress_callback=None,
    ) -> dict[str, str]:
        _ = payload
        assert command == "scan"
        if callable(progress_callback):
            progress_callback(
                {
                    "stage": "scan_symbol_fetching",
                    "message": "Fetching SPY option quotes.",
                    "symbol": "SPY",
                    "current": 0,
                    "total": 1,
                    "unit": "symbols",
                }
            )
        scan_entered.set()
        assert release_scan.wait(timeout=5.0)
        return {"status": "ok", "command": command}

    app = create_war_room_app(snapshot_provider=snapshot_provider, command_runner=command_runner)
    client = TestClient(app)
    assert client.post("/api/war-room/arm", json={"phrase": "ARM"}).status_code == 204
    assert client.post("/api/war-room/commands/scan", json={"async": True}).status_code == 202
    assert scan_entered.wait(timeout=3.0)

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{host}:{port}"

    ready = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/war-room", timeout=0.2) as response:
                if response.status == 200:
                    ready = True
                    break
        except URLError:
            time.sleep(0.05)

    if not ready:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("live_server did not become ready within 5 seconds")

    try:
        with sync_playwright() as p:
            browser = _launch_chromium_or_skip(p)
            page = browser.new_page()
            page.goto(f"{base_url}/war-room")

            page.wait_for_selector("[data-command-overlay]:not([hidden])")
            page.wait_for_function(
                "() => document.querySelector('[data-overlay-command]')?.textContent.includes('SCAN')"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-overlay-events]')?.textContent.includes('Fetching SPY option quotes')"
            )

            release_scan.set()
            page.wait_for_function(
                "() => document.querySelector('[data-command-overlay]')?.hidden === true"
            )
            page.wait_for_function(
                "() => document.querySelector('[data-mission-log] li')?.textContent.includes('SCAN ok')"
            )
            browser.close()
    finally:
        release_scan.set()
        server.should_exit = True
        thread.join(timeout=5)
