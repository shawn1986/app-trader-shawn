from __future__ import annotations

import json
import subprocess
from typing import Any

from trader_shawn.ai.base import AiProvider, AiProviderError


class ClaudeCliAdapter(AiProvider):
    def __init__(self, command: str = "claude", timeout_seconds: int = 15) -> None:
        self._command = command
        self._timeout_seconds = timeout_seconds

    def request(self, prompt: str) -> dict[str, Any]:
        completed = subprocess.run(
            [self._command, "-p", "--output-format", "json", prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self._timeout_seconds,
            check=True,
        )
        return _parse_stdout("claude", completed.stdout, completed.stderr)


def _parse_stdout(provider: str, stdout: str, stderr: str) -> dict[str, Any]:
    if not stdout.strip():
        raise AiProviderError(provider, "empty stdout", stdout=stdout, stderr=stderr)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AiProviderError(provider, f"malformed json: {exc.msg}", stdout=stdout, stderr=stderr) from exc
    if not isinstance(payload, dict):
        raise AiProviderError(provider, "expected top-level JSON object", stdout=stdout, stderr=stderr)
    return payload
