from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any


RuntimeCommandRunner = Callable[[str, Any], dict[str, Any]]
QuoteCollector = Callable[[Any], dict[str, Any]]


class AutomationRunner:
    _PROFILES: dict[str, tuple[str, ...]] = {
        "paper-observe": ("collect-quotes", "decide", "manage"),
    }
    _SUCCESS_STATUSES = {"ok", "no_candidates", "submitted"}
    _PARTIAL_STATUSES = {"partial", "warning"}

    def __init__(
        self,
        *,
        runtime: Any,
        command_runner: RuntimeCommandRunner,
        quote_collector: QuoteCollector,
    ) -> None:
        self._runtime = runtime
        self._command_runner = command_runner
        self._quote_collector = quote_collector

    def run_once(self, *, profile: str) -> dict[str, Any]:
        commands = self._PROFILES.get(profile)
        if commands is None:
            return {
                "status": "error",
                "reason": "unsupported_profile",
                "profile": profile,
            }
        mode = str(getattr(getattr(self._runtime, "settings", None), "mode", "unknown"))
        if profile == "paper-observe" and mode != "paper":
            return {
                "status": "error",
                "reason": "profile_requires_paper_mode",
                "profile": profile,
                "mode": mode,
            }

        started_at = datetime.now(UTC).isoformat()
        try:
            setattr(self._runtime, "automation_started_at", started_at)
        except Exception:
            pass
        steps: list[dict[str, Any]] = []
        status = "ok"
        for command in commands:
            try:
                result = (
                    self._quote_collector(self._runtime)
                    if command == "collect-quotes"
                    else self._command_runner(command, self._runtime)
                )
            except Exception as exc:
                result = {
                    "status": "error",
                    "reason": "automation_step_failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc) or type(exc).__name__,
                }
            step = {
                "command": command,
                "status": str(result.get("status", "unknown")),
                "result": result,
            }
            steps.append(step)
            if step["status"] in self._SUCCESS_STATUSES:
                continue
            if step["status"] in self._PARTIAL_STATUSES and status != "error":
                status = "partial"
                continue
            status = "error"

        return {
            "status": status,
            "profile": profile,
            "mode": mode,
            "auto_trade_enabled": False,
            "started_at": started_at,
            "steps": steps,
        }
