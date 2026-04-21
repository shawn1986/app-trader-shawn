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
