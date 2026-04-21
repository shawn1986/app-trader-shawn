from __future__ import annotations

import time
from typing import Any

from trader_shawn.war_room.commands import ArmedSessionStore


def test_armed_session_store_prune_handles_mutation_during_expiry_check() -> None:
    store = ArmedSessionStore(ttl_seconds=900)

    class _MutatingExpiry:
        def __init__(self, tokens: dict[str, Any]) -> None:
            self._tokens = tokens
            self._mutated = False

        def __le__(self, other: object) -> bool:
            if not self._mutated:
                self._tokens["late-token"] = time.time() + 60
                self._mutated = True
            _ = other
            return False

    tokens: dict[str, Any] = {
        "stable-token": time.time() + 60,
    }
    tokens["mutating-token"] = _MutatingExpiry(tokens)
    store._tokens = tokens

    assert store.is_armed("stable-token") is True

