from __future__ import annotations

from dataclasses import dataclass
from subprocess import CalledProcessError, TimeoutExpired
from typing import Any, Protocol


class AiProvider(Protocol):
    def request(self, prompt: str) -> dict[str, Any]:
        """Return a JSON-compatible response payload for the prompt."""


@dataclass(slots=True)
class AiProviderError(Exception):
    provider: str
    reason: str
    stdout: str = ""
    stderr: str = ""

    def __str__(self) -> str:
        return f"{self.provider}: {self.reason}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "failure_type": type(self).__name__,
            "reason": self.reason,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def provider_error_from_runtime_failure(provider: str, exc: Exception) -> AiProviderError:
    if isinstance(exc, AiProviderError):
        return exc
    if isinstance(exc, TimeoutExpired):
        return AiProviderError(
            provider,
            "provider command timed out",
            stdout=_coerce_stream_value(exc.stdout),
            stderr=_coerce_stream_value(exc.stderr),
        )
    if isinstance(exc, CalledProcessError):
        return AiProviderError(
            provider,
            f"provider command failed with exit code {exc.returncode}",
            stdout=_coerce_stream_value(exc.stdout),
            stderr=_coerce_stream_value(exc.stderr),
        )
    return AiProviderError(provider, str(exc) or type(exc).__name__)


def _coerce_stream_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
