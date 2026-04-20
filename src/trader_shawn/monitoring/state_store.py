from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StateStoreError(ValueError):
    """Raised when persisted monitor state cannot be loaded safely."""


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateStoreError(f"invalid state file: {self._path}") from exc
        if not isinstance(data, dict):
            raise StateStoreError(
                f"state file must contain a JSON object: {self._path}"
            )
        return data

    def save(self, state: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(state, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self._path)
