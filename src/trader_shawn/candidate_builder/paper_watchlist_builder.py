from __future__ import annotations

from datetime import date
from typing import Any

from trader_shawn.candidate_builder.credit_spread_builder import (
    CandidateFilterSettings,
    DEFAULT_FILTERS,
    _coerce_filter_settings,
)
from trader_shawn.domain.models import OptionQuote, PaperWatchlistEntry
from trader_shawn.events.earnings_calendar import EarningsCalendar


def build_paper_watchlist(
    ticker: str,
    dte: int,
    quotes: list[OptionQuote],
    *,
    earnings_calendar: EarningsCalendar | None = None,
    as_of: date | str | None = None,
    filters: Any | None = None,
) -> list[PaperWatchlistEntry]:
    watchlist: list[PaperWatchlistEntry] = []
    filter_settings = _coerce_filter_settings(filters)

    watchlist.extend(
        _build_watchlist_for_right(
            ticker,
            dte,
            quotes,
            right="P",
            strategy="bull_put_credit_spread",
            short_leg_order="desc",
            earnings_calendar=earnings_calendar,
            as_of=as_of,
            filters=filter_settings,
        )
    )
    watchlist.extend(
        _build_watchlist_for_right(
            ticker,
            dte,
            quotes,
            right="C",
            strategy="bear_call_credit_spread",
            short_leg_order="asc",
            earnings_calendar=earnings_calendar,
            as_of=as_of,
            filters=filter_settings,
        )
    )

    return sorted(
        watchlist,
        key=lambda entry: (entry.expiry, entry.ticker, entry.strategy, entry.width),
    )


def _build_watchlist_for_right(
    ticker: str,
    dte: int,
    quotes: list[OptionQuote],
    *,
    right: str,
    strategy: str,
    short_leg_order: str,
    earnings_calendar: EarningsCalendar | None,
    as_of: date | str | None,
    filters: CandidateFilterSettings,
) -> list[PaperWatchlistEntry]:
    same_side = sorted(
        (
            quote
            for quote in quotes
            if quote.ticker == ticker and quote.right == right
        ),
        key=lambda quote: (quote.expiry, quote.strike),
    )
    if len(same_side) < 2:
        return []

    expiries = sorted({quote.expiry for quote in same_side})
    watchlist: list[PaperWatchlistEntry] = []
    for expiry in expiries:
        expiry_quotes = [quote for quote in same_side if quote.expiry == expiry]
        if len(expiry_quotes) < 2:
            continue

        short_leg = (
            max(expiry_quotes, key=lambda quote: quote.strike)
            if short_leg_order == "desc"
            else min(expiry_quotes, key=lambda quote: quote.strike)
        )
        event_blocked = _has_blocking_event(
            ticker,
            expiry,
            earnings_calendar=earnings_calendar,
            as_of=as_of,
        )
        for long_leg in sorted(
            (quote for quote in expiry_quotes if quote is not short_leg),
            key=lambda quote: abs(short_leg.strike - quote.strike),
        ):
            width = abs(short_leg.strike - long_leg.strike)
            if width <= 0 or width > filters.max_width:
                continue
            if right == "P" and short_leg.strike <= long_leg.strike:
                continue
            if right == "C" and short_leg.strike >= long_leg.strike:
                continue

            watchlist.append(
                PaperWatchlistEntry(
                    ticker=ticker,
                    strategy=strategy,
                    expiry=expiry,
                    dte=dte,
                    short_strike=float(short_leg.strike),
                    long_strike=float(long_leg.strike),
                    width=float(width),
                    short_delta=short_leg.delta,
                    flags=_observation_flags(
                        short_leg,
                        long_leg,
                        event_blocked=event_blocked,
                        filters=filters,
                    ),
                )
            )
    return watchlist


def _observation_flags(
    short_leg: OptionQuote,
    long_leg: OptionQuote,
    *,
    event_blocked: bool,
    filters: CandidateFilterSettings = DEFAULT_FILTERS,
) -> list[str]:
    flags: list[str] = []

    if short_leg.delta is None:
        flags.append("missing_delta")
    elif not filters.min_abs_delta <= abs(short_leg.delta) <= filters.max_abs_delta:
        flags.append("short_delta_out_of_range")

    if short_leg.open_interest < filters.min_open_interest or long_leg.open_interest < filters.min_open_interest:
        flags.append("low_open_interest")

    if short_leg.volume < filters.min_volume or long_leg.volume < filters.min_volume:
        flags.append("low_volume")

    short_mid = (short_leg.bid + short_leg.ask) / 2
    long_mid = (long_leg.bid + long_leg.ask) / 2
    if _has_missing_market_prices(short_leg) or _has_missing_market_prices(long_leg):
        flags.append("missing_market_prices")
    else:
        credit = short_mid - long_mid
        if credit <= 0:
            flags.append("non_positive_credit")
        else:
            bid_ask_ratio = ((short_leg.ask - short_leg.bid) + (long_leg.ask - long_leg.bid)) / credit
            if bid_ask_ratio > filters.max_bid_ask_ratio:
                flags.append("wide_market")

    if event_blocked:
        flags.append("blocked_by_event")

    if not flags:
        flags.append("formal_candidate_filters_not_met")

    return flags


def _has_missing_market_prices(quote: OptionQuote) -> bool:
    return quote.bid <= 0 or quote.ask <= 0


def _has_blocking_event(
    ticker: str,
    expiry: str,
    *,
    earnings_calendar: EarningsCalendar | None,
    as_of: date | str | None,
) -> bool:
    if earnings_calendar is None:
        return False
    start = date.fromisoformat(as_of) if isinstance(as_of, str) else (as_of or date.today())
    return earnings_calendar.has_blocking_event(
        ticker,
        start,
        date.fromisoformat(expiry),
    )
