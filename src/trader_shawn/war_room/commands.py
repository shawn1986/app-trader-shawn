from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

from trader_shawn.app import (
    _decide_command,
    _manage_command,
    _scan_command,
    _trade_command,
    run_cli_command_with_runtime,
)


RuntimeCommand = Callable[[], dict[str, Any]]


class UnsupportedWarRoomCommand(ValueError):
    def __init__(self, command: str) -> None:
        self.command = command
        super().__init__(f"Unsupported command: {command}")


class ArmedSessionStore:
    def __init__(self, ttl_seconds: int = 900) -> None:
        self._ttl_seconds = ttl_seconds
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def arm(self) -> str:
        with self._lock:
            self._prune_expired_tokens_locked()
            token = secrets.token_urlsafe(32)
            self._tokens[token] = self._expires_at()
            return token

    def is_armed(self, token: str | None) -> bool:
        if token is None:
            return False
        with self._lock:
            self._prune_expired_tokens_locked()
            expires_at = self._tokens.get(token)
            return expires_at is not None and expires_at > time.time()

    def _prune_expired_tokens(self) -> None:
        with self._lock:
            self._prune_expired_tokens_locked()

    def _prune_expired_tokens_locked(self) -> None:
        now = time.time()
        expired_tokens = [token for token, expires_at in list(self._tokens.items()) if expires_at <= now]
        for token in expired_tokens:
            self._tokens.pop(token, None)

    def _expires_at(self) -> float:
        return time.time() + self._ttl_seconds


def run_runtime_command(
    command: str,
    payload: dict[str, Any] | None = None,
    *,
    runtime: Any | None = None,
) -> dict[str, Any]:
    _ = payload
    if runtime is not None:
        if command not in {"scan", "decide", "manage", "trade"}:
            raise UnsupportedWarRoomCommand(command)
        return run_cli_command_with_runtime(command, runtime)

    command_map: dict[str, RuntimeCommand] = {
        "scan": _scan_command,
        "decide": _decide_command,
        "manage": _manage_command,
        "trade": _trade_command,
    }
    handler = command_map.get(command)
    if handler is None:
        raise UnsupportedWarRoomCommand(command)
    return handler()
