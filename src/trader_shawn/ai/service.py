from __future__ import annotations

import json
from typing import Any

from trader_shawn.ai.base import AiProvider
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
        except Exception as exc:
            decision.secondary_payload = {
                "action": "error",
                "reason": "secondary provider failed",
                "error": str(exc),
            }

        return decision
