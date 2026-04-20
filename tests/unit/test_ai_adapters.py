from __future__ import annotations

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
