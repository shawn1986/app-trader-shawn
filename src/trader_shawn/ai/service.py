from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from enum import Enum
import json
from subprocess import SubprocessError
from typing import Any

from trader_shawn.ai.base import AiProvider, AiProviderError, provider_error_from_runtime_failure
from trader_shawn.ai.decision_parser import ParsedDecision, parse_decision


class AiDecisionService:
    def __init__(self, primary: AiProvider, secondary: AiProvider | None = None) -> None:
        self._primary = primary
        self._secondary = secondary

    def decide(self, context: dict[str, Any]) -> ParsedDecision:
        prompt = _decision_prompt(context)
        try:
            primary_payload = self._primary.request(prompt)
        except AiProviderError:
            raise
        except (OSError, SubprocessError) as exc:
            raise provider_error_from_runtime_failure(
                self._provider_name(self._primary),
                exc,
            ) from exc

        decision = parse_decision(primary_payload)

        if self._secondary is None:
            return decision

        try:
            decision.secondary_payload = self._secondary.request(prompt)
        except AiProviderError as exc:
            decision.secondary_payload = exc.to_payload()
        except (OSError, SubprocessError) as exc:
            decision.secondary_payload = provider_error_from_runtime_failure(
                self._provider_name(self._secondary),
                exc,
            ).to_payload()

        return decision

    @staticmethod
    def _provider_name(provider: AiProvider | None) -> str:
        if provider is None:
            return "secondary"

        explicit_name = getattr(provider, "provider_name", None)
        if isinstance(explicit_name, str) and explicit_name.strip():
            return explicit_name

        command = getattr(provider, "_command", None)
        if isinstance(command, str) and command.strip():
            return command

        return type(provider).__name__


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _decision_prompt(context: dict[str, Any]) -> str:
    context_json = json.dumps(context, sort_keys=True, default=_json_default)
    return (
        "You are the Trader Shawn decision engine.\n"
        "Return exactly one JSON object and no markdown, prose, code fences, or comments.\n"
        "Use one of these shapes:\n"
        '{"action": "reject", "reason": "short explanation"}\n'
        '{"action": "hold", "reason": "short explanation"}\n'
        '{"action": "approve", "ticker": "AMD", "strategy": "bull_put_credit_spread", '
        '"expiry": "YYYY-MM-DD", "short_strike": 100.0, "long_strike": 95.0, '
        '"limit_credit": 1.0, "confidence": 0.7, "reason": "short explanation", '
        '"risk_note": "risk summary"}\n'
        "Only approve a candidate that appears in the provided candidates list.\n"
        "Context JSON:\n"
        f"{context_json}"
    )
