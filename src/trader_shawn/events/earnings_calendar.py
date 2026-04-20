from __future__ import annotations

from datetime import date


class EarningsCalendar:
    def __init__(self, events: list[dict]) -> None:
        self._events = [
            {**event, "date": self._coerce_date(event["date"])}
            for event in events
        ]

    def has_blocking_event(self, ticker: str, start: date, end: date) -> bool:
        return any(
            event["ticker"] == ticker and start <= event["date"] <= end
            for event in self._events
        )

    @staticmethod
    def _coerce_date(value: date | str) -> date:
        if isinstance(value, date):
            return value
        return date.fromisoformat(value)
