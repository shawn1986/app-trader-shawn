from __future__ import annotations

from datetime import date

from trader_shawn.candidate_builder.credit_spread_builder import (
    MAX_BID_ASK_RATIO,
    MAX_WIDTH,
    MAX_ABS_DELTA,
    MIN_ABS_DELTA,
    MIN_OPEN_INTEREST,
    MIN_VOLUME,
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
) -> list[PaperWatchlistEntry]:
    watchlist: list[PaperWatchlistEntry] = []

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
            if width <= 0 or width > MAX_WIDTH:
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
                    flags=_observation_flags(short_leg, long_leg, event_blocked=event_blocked),
                )
            )
    return watchlist


def _observation_flags(
    short_leg: OptionQuote,
    long_leg: OptionQuote,
    *,
    event_blocked: bool,
) -> list[str]:
    flags: list[str] = []

    if short_leg.delta is None:
        flags.append("missing_delta")
    elif not MIN_ABS_DELTA <= abs(short_leg.delta) <= MAX_ABS_DELTA:
        flags.append("short_delta_out_of_range")

    if short_leg.open_interest < MIN_OPEN_INTEREST or long_leg.open_interest < MIN_OPEN_INTEREST:
        flags.append("low_open_interest")

    if short_leg.volume < MIN_VOLUME or long_leg.volume < MIN_VOLUME:
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
            if bid_ask_ratio > MAX_BID_ASK_RATIO:
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
