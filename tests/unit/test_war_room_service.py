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


def test_build_war_room_snapshot_handles_naive_now_with_aware_checked_at() -> None:
    snapshot = build_war_room_snapshot(
        dashboard_state={"status": "idle", "last_cycle": {}, "error": None},
        account_snapshot={},
        managed_positions=[],
        position_events=[],
        broker_health={
            "connected": True,
            "latency_ms": 20,
            "checked_at": "2026-04-21T01:00:00+00:00",
            "message": "",
        },
        now=datetime(2026, 4, 21, 1, 0, 20),
    )

    assert snapshot["command_status"]["broker"]["freshness"] == "fresh"
    assert snapshot["threat_level"] == "nominal"


def test_build_war_room_snapshot_prioritizes_hot_positions_by_uncertain_then_closing() -> None:
    snapshot = build_war_room_snapshot(
        dashboard_state={"status": "idle", "last_cycle": {}, "error": None},
        account_snapshot={},
        managed_positions=[
            {
                "position_id": "pos-normal-open",
                "ticker": "AAPL",
                "status": "open",
                "expiry": "2026-05-01",
                "last_known_debit": 0.5,
                "opened_at": "2026-04-20T09:32:00+00:00",
            },
            {
                "position_id": "pos-uncertain-open",
                "ticker": "NVDA",
                "status": "open",
                "expiry": "2026-05-01",
                "last_known_debit": 0.8,
                "opened_at": "2026-04-20T09:33:00+00:00",
            },
            {
                "position_id": "pos-uncertain-closing",
                "ticker": "AMD",
                "status": "closing",
                "expiry": "2026-05-01",
                "last_known_debit": 1.1,
                "opened_at": "2026-04-20T09:31:00+00:00",
            },
            {
                "position_id": "pos-normal-closing",
                "ticker": "TSLA",
                "status": "closing",
                "expiry": "2026-05-01",
                "last_known_debit": 1.0,
                "opened_at": "2026-04-20T09:34:00+00:00",
            },
        ],
        position_events=[
            {
                "position_id": "pos-uncertain-open",
                "event_type": "close_submit_uncertain",
                "payload_json": {},
                "created_at": "2026-04-21T01:00:01+00:00",
            },
            {
                "position_id": "pos-uncertain-closing",
                "event_type": "close_submit_uncertain",
                "payload_json": {},
                "created_at": "2026-04-21T01:00:02+00:00",
            },
            {
                "position_id": "pos-normal-closing",
                "event_type": "close_submitted",
                "payload_json": {},
                "created_at": "2026-04-21T01:00:03+00:00",
            },
        ],
        broker_health={
            "connected": True,
            "latency_ms": 11,
            "checked_at": "2026-04-21T01:00:00+00:00",
            "message": "",
        },
        now=datetime(2026, 4, 21, 1, 0, tzinfo=UTC),
    )

    assert [position["position_id"] for position in snapshot["hot_positions"]] == [
        "pos-uncertain-closing",
        "pos-uncertain-open",
        "pos-normal-closing",
        "pos-normal-open",
    ]
