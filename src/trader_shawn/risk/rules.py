from __future__ import annotations

from pydantic import BaseModel


class GuardResult(BaseModel):
    allowed: bool
    reason: str
