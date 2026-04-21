from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Any

from trader_shawn.app import _decide_command, _manage_command, _scan_command, _trade_command


RuntimeCommand = Callable[[], dict[str, Any]]


class ArmedSessionStore:
    def __init__(self, ttl_seconds: int = 900) -> None:
        self._ttl_seconds = ttl_seconds
        self._tokens: dict[str, float] = {}

    def arm(self) -> str:
        self._prune_expired_tokens()
        token = secrets.token_urlsafe(32)
        self._tokens[token] = self._expires_at()
        return token

    def is_armed(self, token: str | None) -> bool:
        if token is None:
            return False
        self._prune_expired_tokens()
        expires_at = self._tokens.get(token)
        if expires_at is None:
            return False
        return expires_at > time.time()

    def _prune_expired_tokens(self) -> None:
        now = time.time()
        expired_tokens = [token for token, expires_at in self._tokens.items() if expires_at <= now]
        for token in expired_tokens:
            del self._tokens[token]

    def _expires_at(self) -> float:
        return time.time() + self._ttl_seconds


def run_runtime_command(command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = payload
    command_map: dict[str, RuntimeCommand] = {
        "scan": _scan_command,
        "decide": _decide_command,
        "manage": _manage_command,
        "trade": _trade_command,
    }
    handler = command_map.get(command)
    if handler is None:
        raise ValueError(f"Unsupported command: {command}")
    return handler()
