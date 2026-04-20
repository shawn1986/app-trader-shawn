from __future__ import annotations

from typing import Any, Protocol


class AiProvider(Protocol):
    def request(self, prompt: str) -> dict[str, Any]:
        """Return a JSON-compatible response payload for the prompt."""
