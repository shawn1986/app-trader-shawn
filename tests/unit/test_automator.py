from __future__ import annotations

from types import SimpleNamespace

from trader_shawn.automation.runner import AutomationRunner


def test_paper_observe_profile_runs_collect_decide_manage_without_trade() -> None:
    calls: list[str] = []

    runner = AutomationRunner(
        runtime=SimpleNamespace(settings=SimpleNamespace(mode="paper")),
        command_runner=lambda command, runtime: calls.append(command) or {"status": "ok"},
        quote_collector=lambda runtime: calls.append("collect-quotes") or {"status": "ok"},
    )

    result = runner.run_once(profile="paper-observe")

    assert result["status"] == "ok"
    assert result["profile"] == "paper-observe"
    assert calls == ["collect-quotes", "decide", "manage"]
    assert [step["command"] for step in result["steps"]] == calls


def test_paper_observe_profile_rejects_live_mode() -> None:
    calls: list[str] = []

    runner = AutomationRunner(
        runtime=SimpleNamespace(settings=SimpleNamespace(mode="live")),
        command_runner=lambda command, runtime: calls.append(command) or {"status": "ok"},
        quote_collector=lambda runtime: calls.append("collect-quotes") or {"status": "ok"},
    )

    result = runner.run_once(profile="paper-observe")

    assert result == {
        "status": "error",
        "reason": "profile_requires_paper_mode",
        "profile": "paper-observe",
        "mode": "live",
    }
    assert calls == []


def test_submitted_step_counts_as_successful_cycle() -> None:
    runner = AutomationRunner(
        runtime=SimpleNamespace(settings=SimpleNamespace(mode="paper")),
        command_runner=lambda command, runtime: {"status": "submitted" if command == "manage" else "ok"},
        quote_collector=lambda runtime: {"status": "ok"},
    )

    result = runner.run_once(profile="paper-observe")

    assert result["status"] == "ok"


def test_step_error_propagates_to_cycle_error() -> None:
    runner = AutomationRunner(
        runtime=SimpleNamespace(settings=SimpleNamespace(mode="paper")),
        command_runner=lambda command, runtime: {"status": "ok"},
        quote_collector=lambda runtime: {
            "status": "error",
            "reason": "market_data_unavailable",
        },
    )

    result = runner.run_once(profile="paper-observe")

    assert result["status"] == "error"
    assert result["steps"][0]["status"] == "error"


def test_command_error_status_propagates_to_cycle_error() -> None:
    runner = AutomationRunner(
        runtime=SimpleNamespace(settings=SimpleNamespace(mode="paper")),
        command_runner=lambda command, runtime: {
            "status": "decision_error" if command == "decide" else "ok"
        },
        quote_collector=lambda runtime: {"status": "ok"},
    )

    result = runner.run_once(profile="paper-observe")

    assert result["status"] == "error"
    assert result["steps"][1]["status"] == "decision_error"


def test_partial_step_status_remains_partial_cycle() -> None:
    runner = AutomationRunner(
        runtime=SimpleNamespace(settings=SimpleNamespace(mode="paper")),
        command_runner=lambda command, runtime: {"status": "ok"},
        quote_collector=lambda runtime: {"status": "partial"},
    )

    result = runner.run_once(profile="paper-observe")

    assert result["status"] == "partial"


def test_step_exception_becomes_structured_error() -> None:
    runner = AutomationRunner(
        runtime=SimpleNamespace(settings=SimpleNamespace(mode="paper")),
        command_runner=lambda command, runtime: {"status": "ok"},
        quote_collector=lambda runtime: (_ for _ in ()).throw(RuntimeError("sqlite busy")),
    )

    result = runner.run_once(profile="paper-observe")

    assert result["status"] == "error"
    assert result["steps"][0]["command"] == "collect-quotes"
    assert result["steps"][0]["status"] == "error"
    assert result["steps"][0]["result"]["reason"] == "automation_step_failed"
    assert result["steps"][0]["result"]["message"] == "sqlite busy"


def test_started_at_is_set_before_running_steps() -> None:
    observed_started_at: list[str | None] = []

    def command_runner(command, runtime):
        observed_started_at.append(getattr(runtime, "automation_started_at", None))
        return {"status": "ok"}

    runtime = SimpleNamespace(settings=SimpleNamespace(mode="paper"))
    runner = AutomationRunner(
        runtime=runtime,
        command_runner=command_runner,
        quote_collector=lambda runtime: {"status": "ok"},
    )

    result = runner.run_once(profile="paper-observe")

    assert result["started_at"]
    assert observed_started_at == [result["started_at"], result["started_at"]]


def test_unsupported_profile_returns_error() -> None:
    runner = AutomationRunner(
        runtime=SimpleNamespace(settings=SimpleNamespace(mode="paper")),
        command_runner=lambda command, runtime: {"status": "ok"},
        quote_collector=lambda runtime: {"status": "ok"},
    )

    result = runner.run_once(profile="does-not-exist")

    assert result == {
        "status": "error",
        "reason": "unsupported_profile",
        "profile": "does-not-exist",
    }
