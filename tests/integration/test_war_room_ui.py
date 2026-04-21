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

    def command_runner(command: str, payload: dict[str, object] | None = None) -> dict[str, str]:
        _ = payload
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
    assert "Command Status" in html
    assert "Threat Rail" in html
    assert "Type ARM to unlock" in html


def test_war_room_static_assets_are_available() -> None:
    client = TestClient(create_war_room_app(snapshot_provider=lambda: {}))

    css_response = client.get("/static/war_room.css")
    js_response = client.get("/static/war_room.js")

    assert css_response.status_code == 200
    assert js_response.status_code == 200


def test_war_room_unlocks_controls_and_refreshes_threat_level(live_server) -> None:
    with sync_playwright() as p:
        browser = _launch_chromium_or_skip(p)
        page = browser.new_page()
        page.goto(f"{live_server}/war-room")
        page.wait_for_function(
            "() => document.querySelector('[data-threat-level]')?.textContent.trim() === 'Warning'"
        )

        page.fill("[data-arm-input]", "ARM")
        page.click("[data-arm-submit]")
        page.wait_for_selector('[data-command="manage"]:not([disabled])')

        page.click('[data-command="manage"]')
        page.wait_for_function(
            "() => document.querySelector('[data-mission-log] li')?.textContent.includes('MANAGE ok')"
        )
        page.wait_for_function(
            "() => document.querySelector('[data-threat-level]')?.textContent.trim() === 'Critical'"
        )

        page.click('[data-command="trade"]')
        page.wait_for_selector("[data-trade-confirm]:not([hidden])")
        page.click("[data-trade-confirm-submit]")
        page.wait_for_selector("[data-trade-confirm][hidden]")
        page.wait_for_function(
            "() => document.querySelector('[data-mission-log] li')?.textContent.includes('TRADE ok')"
        )
        page.wait_for_function(
            "() => document.querySelector('[data-threat-level]')?.textContent.trim() === 'Nominal'"
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
        page.click("[data-arm-submit]")
        page.wait_for_selector('[data-command="trade"]:not([disabled])')

        page.context.clear_cookies()

        page.click('[data-command="trade"]')
        page.wait_for_selector("[data-trade-confirm]:not([hidden])")
        page.click("[data-trade-confirm-submit]")

        page.wait_for_function("() => document.body.dataset.mode === 'monitoring'")
        page.wait_for_selector('[data-command="manage"][disabled]')
        page.wait_for_selector("[data-trade-confirm][hidden]")
        page.wait_for_function(
            "() => document.querySelector('[data-mission-log] li')?.textContent.includes('armed_mode_required')"
        )

        browser.close()
