from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from trader_shawn.domain.models import CandidateSpread, OptionQuote
from trader_shawn.events.earnings_calendar import EarningsCalendar

MIN_OPEN_INTEREST = 100
MIN_VOLUME = 50
MIN_ABS_DELTA = 0.15
MAX_ABS_DELTA = 0.25
MAX_WIDTH = 5
MAX_BID_ASK_RATIO = 0.15


@dataclass(frozen=True, slots=True)
class CandidateFilterSettings:
    min_open_interest: int = MIN_OPEN_INTEREST
    min_volume: int = MIN_VOLUME
    min_abs_delta: float = MIN_ABS_DELTA
    max_abs_delta: float = MAX_ABS_DELTA
    max_width: float = MAX_WIDTH
    max_bid_ask_ratio: float = MAX_BID_ASK_RATIO


DEFAULT_FILTERS = CandidateFilterSettings()


def build_candidates(
    ticker: str,
    dte: int,
    quotes: list[OptionQuote],
    *,
    earnings_calendar: EarningsCalendar | None = None,
    as_of: date | None = None,
    filters: Any | None = None,
) -> list[CandidateSpread]:
    candidates: list[CandidateSpread] = []
    filter_settings = _coerce_filter_settings(filters)

    candidates.extend(
        _build_candidates_for_right(
            ticker,
            dte,
            quotes,
            right="P",
            strategy="bull_put_credit_spread",
            width_fn=lambda short_leg, long_leg: short_leg.strike - long_leg.strike,
            earnings_calendar=earnings_calendar,
            as_of=as_of,
            filters=filter_settings,
        )
    )
    candidates.extend(
        _build_candidates_for_right(
            ticker,
            dte,
            quotes,
            right="C",
            strategy="bear_call_credit_spread",
            width_fn=lambda short_leg, long_leg: long_leg.strike - short_leg.strike,
            earnings_calendar=earnings_calendar,
            as_of=as_of,
            filters=filter_settings,
        )
    )

    return sorted(candidates, key=lambda candidate: (candidate.expiry, -candidate.credit, candidate.width))


def _build_candidates_for_right(
    ticker: str,
    dte: int,
    quotes: list[OptionQuote],
    *,
    right: str,
    strategy: str,
    width_fn: Callable[[OptionQuote, OptionQuote], float],
    earnings_calendar: EarningsCalendar | None,
    as_of: date | None,
    filters: CandidateFilterSettings,
) -> list[CandidateSpread]:
    short_leg_candidates = sorted(
        (
            quote
            for quote in quotes
            if quote.ticker == ticker
            and quote.right == right
            and quote.delta is not None
            and filters.min_abs_delta <= abs(quote.delta) <= filters.max_abs_delta
            and quote.open_interest >= filters.min_open_interest
            and quote.volume >= filters.min_volume
        ),
        key=lambda quote: (quote.expiry, quote.strike),
    )
    long_leg_candidates = sorted(
        (quote for quote in quotes if quote.ticker == ticker and quote.right == right),
        key=lambda quote: (quote.expiry, quote.strike),
    )

    candidates: list[CandidateSpread] = []
    for short_leg in short_leg_candidates:
        for long_leg in long_leg_candidates:
            width = width_fn(short_leg, long_leg)
            if short_leg.expiry != long_leg.expiry or width <= 0 or width > filters.max_width:
                continue

            if _has_blocking_event(
                ticker,
                short_leg.expiry,
                earnings_calendar=earnings_calendar,
                as_of=as_of,
            ):
                continue

            short_mid = (short_leg.bid + short_leg.ask) / 2
            long_mid = (long_leg.bid + long_leg.ask) / 2
            credit = short_mid - long_mid
            if credit <= 0:
                continue

            bid_ask_ratio = ((short_leg.ask - short_leg.bid) + (long_leg.ask - long_leg.bid)) / credit
            if bid_ask_ratio > filters.max_bid_ask_ratio:
                continue
            short_delta = abs(short_leg.delta)

            candidates.append(
                CandidateSpread(
                    ticker=ticker,
                    strategy=strategy,
                    short_leg=short_leg,
                    long_leg=long_leg,
                    dte=dte,
                    credit=credit,
                    max_loss=width - credit,
                    width=width,
                    expiry=short_leg.expiry,
                    short_delta=short_delta,
                    pop=1 - short_delta,
                    bid_ask_ratio=bid_ask_ratio,
                )
            )
    return candidates


def _coerce_filter_settings(filters: Any | None) -> CandidateFilterSettings:
    if filters is None:
        return DEFAULT_FILTERS
    if isinstance(filters, CandidateFilterSettings):
        return filters
    return CandidateFilterSettings(
        min_open_interest=int(getattr(filters, "min_open_interest")),
        min_volume=int(getattr(filters, "min_volume")),
        min_abs_delta=float(getattr(filters, "min_abs_delta")),
        max_abs_delta=float(getattr(filters, "max_abs_delta")),
        max_width=float(getattr(filters, "max_width")),
        max_bid_ask_ratio=float(getattr(filters, "max_bid_ask_ratio")),
    )


def _has_blocking_event(
    ticker: str,
    expiry: str,
    *,
    earnings_calendar: EarningsCalendar | None,
    as_of: date | None,
) -> bool:
    if earnings_calendar is None:
        return False
    start = as_of or date.today()
    return earnings_calendar.has_blocking_event(
        ticker,
        start,
        date.fromisoformat(expiry),
    )
