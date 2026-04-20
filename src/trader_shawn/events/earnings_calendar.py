from __future__ import annotations

from datetime import date

class EarningsCalendar:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    def has_blocking_event(self, ticker: str, start: date, end: date) -> bool:
        return any(
            event["ticker"] == ticker and start <= event["date"] <= end
            for event in self._events
        )
