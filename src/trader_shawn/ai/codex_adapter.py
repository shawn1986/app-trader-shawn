from __future__ import annotations

import json
import subprocess
from typing import Any

from trader_shawn.ai.base import AiProvider


class CodexAdapter(AiProvider):
    def __init__(self, command: str = "codex", timeout_seconds: int = 10) -> None:
        self._command = command
        self._timeout_seconds = timeout_seconds

    def request(self, prompt: str) -> dict[str, Any]:
        completed = subprocess.run(
            [self._command, "exec", "--json", prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self._timeout_seconds,
            check=True,
        )
        return json.loads(completed.stdout)
