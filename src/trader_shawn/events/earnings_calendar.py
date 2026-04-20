from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    symbol: str
    event_type: str
    event_date: str
    blocks_new_positions: bool = True


class EarningsCalendar:
    def __init__(self, events: Iterable[CalendarEvent] | None = None) -> None:
        self._events = list(events or [])

    def blocking_events_for(self, symbol: str, as_of: str | None = None) -> list[CalendarEvent]:
        return [
            event
            for event in self._events
            if event.symbol == symbol
            and event.blocks_new_positions
            and (as_of is None or event.event_date >= as_of)
        ]

    def has_blocking_event(self, symbol: str, as_of: str | None = None) -> bool:
        return bool(self.blocking_events_for(symbol, as_of=as_of))
