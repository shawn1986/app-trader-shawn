import socket
import threading

import pytest
import uvicorn
from fastapi.testclient import TestClient
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
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"{live_server}/war-room")

        page.fill("[data-arm-input]", "ARM")
        page.click("[data-arm-submit]")
        page.wait_for_selector('[data-command="manage"]:not([disabled])')

        expect_text = page.locator("[data-threat-level]").inner_text()
        assert expect_text in {"Warning", "Critical", "Nominal"}
        browser.close()
