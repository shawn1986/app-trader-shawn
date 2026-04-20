from __future__ import annotations

import json
import subprocess
from typing import Any

from trader_shawn.ai.base import AiProvider, AiProviderError


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
        return _parse_stdout("codex", completed.stdout, completed.stderr)


def _parse_stdout(provider: str, stdout: str, stderr: str) -> dict[str, Any]:
    if not stdout.strip():
        raise AiProviderError(provider, "empty stdout", stdout=stdout, stderr=stderr)

    stripped_stdout = stdout.strip()
    if "\n" not in stripped_stdout:
        return _parse_json_object(provider, stripped_stdout, stdout, stderr)

    assistant_message = _extract_assistant_message(provider, stdout, stderr)
    return _parse_json_object(provider, assistant_message, stdout, stderr)


def _parse_json_object(
    provider: str,
    raw_json: str,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise AiProviderError(provider, f"malformed json: {exc.msg}", stdout=stdout, stderr=stderr) from exc
    if not isinstance(payload, dict):
        raise AiProviderError(provider, "expected top-level JSON object", stdout=stdout, stderr=stderr)
    return payload


def _extract_assistant_message(provider: str, stdout: str, stderr: str) -> str:
    assistant_text: str | None = None

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AiProviderError(provider, f"malformed json: {exc.msg}", stdout=stdout, stderr=stderr) from exc
        if not isinstance(event, dict):
            raise AiProviderError(provider, "expected top-level JSON object", stdout=stdout, stderr=stderr)

        message = _assistant_message_from_event(event)
        if message is not None:
            assistant_text = message

    if assistant_text is None:
        raise AiProviderError(provider, "missing assistant message payload", stdout=stdout, stderr=stderr)

    return assistant_text


def _assistant_message_from_event(event: dict[str, Any]) -> str | None:
    message = event.get("item")
    if not isinstance(message, dict):
        return None
    if message.get("type") != "message" or message.get("role") != "assistant":
        return None

    content = message.get("content")
    if not isinstance(content, list):
        return None

    output_fragments: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "output_text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            output_fragments.append(text)

    if not output_fragments:
        return None

    return "".join(output_fragments)
