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


def test_war_room_static_assets_are_available() -> None:
    client = TestClient(create_war_room_app(snapshot_provider=lambda: {}))

    css_response = client.get("/static/war_room.css")
    js_response = client.get("/static/war_room.js")

    assert css_response.status_code == 200
    assert js_response.status_code == 200
