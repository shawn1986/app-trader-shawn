from __future__ import annotations

import json
from subprocess import CompletedProcess

import pytest

from trader_shawn.ai.base import AiProviderError
from trader_shawn.ai.claude_cli_adapter import ClaudeCliAdapter
from trader_shawn.ai.codex_adapter import CodexAdapter


@pytest.mark.parametrize(
    ("adapter_cls", "stdout", "message"),
    [
        (ClaudeCliAdapter, "", "empty stdout"),
        (ClaudeCliAdapter, "{", "malformed json"),
        (ClaudeCliAdapter, "[]", "top-level JSON object"),
        (CodexAdapter, "", "empty stdout"),
        (CodexAdapter, "{", "malformed json"),
        (CodexAdapter, "[]", "top-level JSON object"),
    ],
)
def test_cli_adapters_raise_provider_error_for_bad_stdout(
    monkeypatch: pytest.MonkeyPatch,
    adapter_cls: type[ClaudeCliAdapter] | type[CodexAdapter],
    stdout: str,
    message: str,
) -> None:
    def fake_run(*args, **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(args=args[0], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    adapter = adapter_cls(command="fake-cli")

    with pytest.raises(AiProviderError, match=message):
        adapter.request('{"ticker":"AMD"}')


def test_codex_adapter_parses_jsonl_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread_123"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"action":"reject","reason":"too concentrated"}',
                            }
                        ],
                    },
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {"total_tokens": 123}}),
        ]
    )

    def fake_run(*args, **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(args=args[0], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    adapter = CodexAdapter(command="fake-codex")

    assert adapter.request('{"ticker":"AMD"}') == {
        "action": "reject",
        "reason": "too concentrated",
    }


def test_claude_adapter_parses_result_wrapper_json_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": '{"action":"reject","reason":"too concentrated"}',
        }
    )

    def fake_run(*args, **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(args=args[0], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    adapter = ClaudeCliAdapter(command="fake-claude")

    assert adapter.request('{"ticker":"AMD"}') == {
        "action": "reject",
        "reason": "too concentrated",
    }


def test_codex_adapter_rejects_jsonl_without_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread_123"}),
            json.dumps({"type": "turn.completed", "usage": {"total_tokens": 123}}),
        ]
    )

    def fake_run(*args, **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(args=args[0], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    adapter = CodexAdapter(command="fake-codex")

    with pytest.raises(AiProviderError, match="assistant message payload"):
        adapter.request('{"ticker":"AMD"}')
