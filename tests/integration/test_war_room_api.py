from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from trader_shawn.monitoring.audit_logger import AuditLogger
from trader_shawn.war_room.web import create_war_room_app
from trader_shawn.war_room.web import create_default_snapshot_provider


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


def test_arm_endpoint_rejects_invalid_phrase() -> None:
    app = create_war_room_app(snapshot_provider=lambda: {})
    client = TestClient(app)

    response = client.post("/api/war-room/arm", json={"phrase": "NOPE"})

    assert response.status_code == 403
    assert response.json()["reason"] == "invalid_arm_phrase"


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


def test_snapshot_endpoint_uses_default_provider_with_runtime_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dashboard_path = tmp_path / "dashboard.json"
    dashboard_path.write_text(
        '{"status":"updated","last_cycle":{"status":"ok","managed_count":1},"error":null}',
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
    )

    class StubAccountService:
        def fetch_account_snapshot(self) -> dict[str, float]:
            return {
                "net_liquidation": 50_000.0,
                "unrealized_pnl": -100.0,
                "open_risk": 3_000.0,
                "new_positions_today": 1,
            }

    runtime = SimpleNamespace(
        dashboard_state_path=dashboard_path,
        settings=SimpleNamespace(audit_db_path=tmp_path / "audit.db", mode="paper"),
        account_service=StubAccountService(),
    )
    monkeypatch.setattr(
        "trader_shawn.war_room.web.build_cli_runtime",
        lambda: runtime,
    )

    app = create_war_room_app(snapshot_provider=create_default_snapshot_provider())
    client = TestClient(app)

    response = client.get("/api/war-room/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_deck"]["active_managed_positions"] == 1
    assert payload["command_status"]["last_cycle"]["status"] == "ok"
    assert payload["mission_log"][0]["event_type"] == "close_submit_uncertain"


def test_snapshot_threat_uses_active_position_latest_events_not_truncated_mission_log(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dashboard_path = tmp_path / "dashboard.json"
    dashboard_path.write_text(
        '{"status":"updated","last_cycle":{"status":"ok","managed_count":1},"error":null}',
        encoding="utf-8",
    )
    audit_logger = AuditLogger(tmp_path / "audit.db")
    audit_logger.upsert_managed_position(
        {
            "position_id": "pos-active",
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
        "pos-active",
        "close_submit_uncertain",
        {"error": "submit temporarily unavailable"},
        created_at="2026-04-21T01:00:00+00:00",
    )
    for index in range(10):
        position_id = f"pos-closed-{index}"
        audit_logger.upsert_managed_position(
            {
                "position_id": position_id,
                "ticker": f"SYM{index}",
                "strategy": "bull_put_credit_spread",
                "expiry": "2026-05-01",
                "short_strike": 110.0,
                "long_strike": 105.0,
                "quantity": 1,
                "entry_credit": 1.0,
                "mode": "paper",
                "status": "closed",
                "opened_at": "2026-04-20T09:31:00+00:00",
                "closed_at": "2026-04-21T01:00:00+00:00",
                "broker_fingerprint": f"SYM{index}|2026-05-01|P|110.0|105.0|1",
            }
        )
        audit_logger.record_position_event(
            position_id,
            "closed",
            {"note": "noise"},
            created_at=f"2026-04-21T01:{index + 1:02d}:00+00:00",
        )

    class StubAccountService:
        def fetch_account_snapshot(self) -> dict[str, float]:
            return {
                "net_liquidation": 50_000.0,
                "unrealized_pnl": -100.0,
                "open_risk": 3_000.0,
                "new_positions_today": 1,
            }

    runtime = SimpleNamespace(
        dashboard_state_path=dashboard_path,
        settings=SimpleNamespace(audit_db_path=tmp_path / "audit.db", mode="paper"),
        account_service=StubAccountService(),
    )
    monkeypatch.setattr(
        "trader_shawn.war_room.web.build_cli_runtime",
        lambda: runtime,
    )

    app = create_war_room_app(snapshot_provider=create_default_snapshot_provider())
    client = TestClient(app)

    response = client.get("/api/war-room/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["threat_level"] == "warning"
    assert payload["hot_positions"][0]["position_id"] == "pos-active"
    assert payload["hot_positions"][0]["latest_event_type"] == "close_submit_uncertain"
    assert len(payload["mission_log"]) == 10
    assert all(item["position_id"] != "pos-active" for item in payload["mission_log"])


def test_create_war_room_app_starts_when_default_provider_creation_fails(monkeypatch) -> None:
    def _broken_default_provider():
        raise RuntimeError("invalid runtime config")

    monkeypatch.setattr(
        "trader_shawn.war_room.web.create_default_snapshot_provider",
        _broken_default_provider,
    )

    app = create_war_room_app()
    client = TestClient(app)

    response = client.get("/api/war-room/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["threat_level"] == "warning"
    assert payload["command_status"]["broker"]["state"] == "degraded"
    assert payload["command_status"]["broker"]["message"] == "snapshot_provider_unavailable"
