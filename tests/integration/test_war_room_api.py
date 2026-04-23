from pathlib import Path
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

import trader_shawn.war_room.web as web_module
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


def test_root_redirects_to_war_room_shell() -> None:
    client = TestClient(create_war_room_app(snapshot_provider=lambda: {}))

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/war-room"


def test_favicon_returns_no_content() -> None:
    client = TestClient(create_war_room_app(snapshot_provider=lambda: {}))

    response = client.get("/favicon.ico")

    assert response.status_code == 204
    assert response.content == b""


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


def test_command_endpoint_maps_unsupported_command_exception_to_404() -> None:
    app = create_war_room_app(snapshot_provider=lambda: {})
    client = TestClient(app)

    arm = client.post("/api/war-room/arm", json={"phrase": "ARM"})
    assert arm.status_code == 204

    response = client.post("/api/war-room/commands/liquidate", json={})

    assert response.status_code == 404
    assert response.json() == {
        "reason": "unsupported_command",
        "command": "liquidate",
    }


def test_command_endpoint_does_not_map_runtime_value_error_to_404() -> None:
    def command_runner(command: str, payload=None) -> dict[str, str]:
        _ = payload
        if command == "manage":
            raise ValueError("transient manage failure")
        return {"status": "ok", "command": command}

    app = create_war_room_app(snapshot_provider=lambda: {}, command_runner=command_runner)
    client = TestClient(app, raise_server_exceptions=False)

    arm = client.post("/api/war-room/arm", json={"phrase": "ARM"})
    assert arm.status_code == 204

    response = client.post("/api/war-room/commands/manage", json={})

    assert response.status_code == 500
    assert "Internal Server Error" in response.text


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


def test_default_app_uses_one_runtime_for_snapshot_and_default_commands(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dashboard_path = tmp_path / "dashboard.json"
    dashboard_path.write_text(
        '{"status":"idle","last_cycle":{},"error":null}',
        encoding="utf-8",
    )
    calls = {"count": 0}

    class StubScanner:
        def scan_candidates(self, symbols: list[str]) -> list[object]:
            _ = symbols
            return []

    class StubAccountService:
        def fetch_account_snapshot(self) -> dict[str, float]:
            return {
                "net_liquidation": 50_000.0,
                "unrealized_pnl": 0.0,
                "open_risk": 0.0,
                "new_positions_today": 0,
            }

    runtime = SimpleNamespace(
        config_dir=tmp_path / "config",
        dashboard_state_path=dashboard_path,
        settings=SimpleNamespace(
            audit_db_path=tmp_path / "audit.db",
            mode="paper",
            live_enabled=False,
            symbols=["AMD"],
        ),
        scanner=StubScanner(),
        account_service=StubAccountService(),
    )

    def _build_runtime():
        calls["count"] += 1
        return runtime

    monkeypatch.setattr("trader_shawn.war_room.web.build_cli_runtime", _build_runtime)
    monkeypatch.setattr("trader_shawn.app.build_cli_runtime", _build_runtime)

    app = create_war_room_app()
    client = TestClient(app)

    snapshot_response = client.get("/api/war-room/snapshot")
    assert snapshot_response.status_code == 200

    arm_response = client.post("/api/war-room/arm", json={"phrase": "ARM"})
    assert arm_response.status_code == 204

    command_response = client.post("/api/war-room/commands/scan", json={})
    assert command_response.status_code == 200
    assert command_response.json()["status"] == "ok"

    assert calls["count"] == 1


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


def test_create_war_room_app_retries_lazy_default_provider_creation_after_failure(monkeypatch) -> None:
    attempts = {"count": 0}

    def _flaky_default_provider():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("startup race")
        return lambda: {
            "generated_at": "2026-04-21T01:02:00+00:00",
            "threat_level": "nominal",
            "command_status": {"broker": {"state": "ok"}},
            "risk_deck": {"open_risk": 100.0},
            "hot_positions": [],
            "mission_log": [],
            "threat_rail": {"level": "nominal"},
        }

    monkeypatch.setattr(
        "trader_shawn.war_room.web.create_default_snapshot_provider",
        _flaky_default_provider,
    )

    app = create_war_room_app()
    client = TestClient(app)

    first = client.get("/api/war-room/snapshot")
    assert first.status_code == 200
    assert first.json()["command_status"]["broker"]["message"] == "snapshot_provider_unavailable"

    second = client.get("/api/war-room/snapshot")
    assert second.status_code == 200
    assert second.json()["threat_level"] == "nominal"
    assert attempts["count"] == 2


def test_default_command_path_returns_structured_config_error_on_runtime_init_failure(
    monkeypatch,
) -> None:
    def _boom_runtime():
        raise RuntimeError("broken config")

    monkeypatch.setattr("trader_shawn.war_room.web.build_cli_runtime", _boom_runtime)
    monkeypatch.setattr("trader_shawn.app.build_cli_runtime", _boom_runtime)

    app = create_war_room_app()
    client = TestClient(app, raise_server_exceptions=False)

    arm = client.post("/api/war-room/arm", json={"phrase": "ARM"})
    assert arm.status_code == 204

    response = client.post("/api/war-room/commands/scan", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["reason"] == "config_load_failed"
    assert payload["error_type"] == "RuntimeError"
    assert payload["message"] == "broken config"


def test_shared_default_runtime_initializes_once_under_concurrent_first_access(monkeypatch) -> None:
    start = threading.Barrier(3)
    release_runtime = threading.Event()
    runtime_entered = threading.Event()
    build_calls = {"count": 0}
    provider_calls = {"count": 0}
    shared_runtime = object()

    def _blocking_runtime_builder():
        build_calls["count"] += 1
        runtime_entered.set()
        assert release_runtime.wait(timeout=3.0)
        return shared_runtime

    def _provider_factory(*, runtime=None):
        provider_calls["count"] += 1
        assert runtime is shared_runtime
        return lambda: {"status": "ok", "source": "snapshot"}

    def _runtime_runner(command, payload=None, *, runtime=None):
        _ = payload
        assert command == "scan"
        assert runtime is shared_runtime
        return {"status": "ok", "source": "command"}

    monkeypatch.setattr("trader_shawn.war_room.web.build_cli_runtime", _blocking_runtime_builder)
    monkeypatch.setattr("trader_shawn.war_room.web.create_default_snapshot_provider", _provider_factory)
    monkeypatch.setattr("trader_shawn.war_room.web.run_runtime_command", _runtime_runner)

    provider, runner = web_module._lazy_shared_default_provider_and_runner()
    outputs: list[dict[str, str]] = []
    errors: list[Exception] = []

    def _call_provider() -> None:
        start.wait()
        try:
            outputs.append(provider())
        except Exception as exc:  # pragma: no cover - diagnostic guard
            errors.append(exc)

    def _call_runner() -> None:
        start.wait()
        try:
            outputs.append(runner("scan", {}))
        except Exception as exc:  # pragma: no cover - diagnostic guard
            errors.append(exc)

    provider_thread = threading.Thread(target=_call_provider)
    runner_thread = threading.Thread(target=_call_runner)
    provider_thread.start()
    runner_thread.start()
    start.wait()
    assert runtime_entered.wait(timeout=3.0)
    release_runtime.set()
    provider_thread.join(timeout=3.0)
    runner_thread.join(timeout=3.0)

    assert not errors
    assert len(outputs) == 2
    assert build_calls["count"] == 1
    assert provider_calls["count"] == 1


def test_shared_default_runtime_serializes_provider_and_runner_use(monkeypatch) -> None:
    provider_entered = threading.Event()
    release_provider = threading.Event()
    provider_in_progress = threading.Event()
    command_entered = threading.Event()
    runner_started = threading.Event()
    shared_runtime = object()

    def _runtime_builder():
        return shared_runtime

    def _provider_factory(*, runtime=None):
        assert runtime is shared_runtime

        def _provider():
            provider_in_progress.set()
            provider_entered.set()
            assert release_provider.wait(timeout=3.0)
            provider_in_progress.clear()
            return {"status": "ok", "source": "snapshot"}

        return _provider

    def _runtime_runner(command, payload=None, *, runtime=None):
        _ = payload
        assert command == "scan"
        assert runtime is shared_runtime
        command_entered.set()
        assert not provider_in_progress.is_set()
        return {"status": "ok", "source": "command"}

    monkeypatch.setattr("trader_shawn.war_room.web.build_cli_runtime", _runtime_builder)
    monkeypatch.setattr("trader_shawn.war_room.web.create_default_snapshot_provider", _provider_factory)
    monkeypatch.setattr("trader_shawn.war_room.web.run_runtime_command", _runtime_runner)

    provider, runner = web_module._lazy_shared_default_provider_and_runner()
    assert runner("scan", {})["status"] == "ok"
    command_entered.clear()

    errors: list[Exception] = []
    outputs: list[dict[str, str]] = []

    def _call_provider() -> None:
        try:
            outputs.append(provider())
        except Exception as exc:  # pragma: no cover - diagnostic guard
            errors.append(exc)

    def _call_runner() -> None:
        runner_started.set()
        try:
            outputs.append(runner("scan", {}))
        except Exception as exc:  # pragma: no cover - diagnostic guard
            errors.append(exc)

    provider_thread = threading.Thread(target=_call_provider)
    provider_thread.start()
    assert provider_entered.wait(timeout=3.0)

    runner_thread = threading.Thread(target=_call_runner)
    runner_thread.start()
    assert runner_started.wait(timeout=3.0)
    assert command_entered.wait(timeout=0.2) is False

    release_provider.set()
    provider_thread.join(timeout=3.0)
    runner_thread.join(timeout=3.0)

    assert not errors
    assert len(outputs) == 2
    assert command_entered.is_set()
