from __future__ import annotations

from collections.abc import Callable
from datetime import date

from trader_shawn.domain.models import CandidateSpread, OptionQuote
from trader_shawn.events.earnings_calendar import EarningsCalendar

MIN_OPEN_INTEREST = 100
MIN_VOLUME = 50
MIN_ABS_DELTA = 0.15
MAX_ABS_DELTA = 0.25
MAX_WIDTH = 5
MAX_BID_ASK_RATIO = 0.15


def build_candidates(
    ticker: str,
    dte: int,
    quotes: list[OptionQuote],
    *,
    earnings_calendar: EarningsCalendar | None = None,
    as_of: date | None = None,
) -> list[CandidateSpread]:
    candidates: list[CandidateSpread] = []

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
) -> list[CandidateSpread]:
    short_leg_candidates = sorted(
        (
            quote
            for quote in quotes
            if quote.ticker == ticker
            and quote.right == right
            and quote.delta is not None
            and MIN_ABS_DELTA <= abs(quote.delta) <= MAX_ABS_DELTA
            and quote.open_interest >= MIN_OPEN_INTEREST
            and quote.volume >= MIN_VOLUME
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
            if short_leg.expiry != long_leg.expiry or width <= 0 or width > MAX_WIDTH:
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
            if bid_ask_ratio > MAX_BID_ASK_RATIO:
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
