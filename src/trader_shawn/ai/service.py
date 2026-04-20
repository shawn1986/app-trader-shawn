from __future__ import annotations

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
        prompt = json.dumps(context, sort_keys=True)
        decision = parse_decision(self._primary.request(prompt))

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
