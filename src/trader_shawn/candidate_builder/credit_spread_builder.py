from __future__ import annotations

from trader_shawn.domain.models import CandidateSpread, OptionQuote

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
) -> list[CandidateSpread]:
    eligible_quotes = sorted(
        (
            quote
            for quote in quotes
            if quote.ticker == ticker
            and quote.right == "P"
            and quote.delta is not None
            and MIN_ABS_DELTA <= abs(quote.delta) <= MAX_ABS_DELTA
            and quote.open_interest >= MIN_OPEN_INTEREST
            and quote.volume >= MIN_VOLUME
        ),
        key=lambda quote: (-quote.strike, quote.expiry),
    )

    candidates: list[CandidateSpread] = []
    for short_leg in eligible_quotes:
        for long_leg in eligible_quotes:
            width = short_leg.strike - long_leg.strike
            if short_leg.expiry != long_leg.expiry or width <= 0 or width > MAX_WIDTH:
                continue

            short_mid = (short_leg.bid + short_leg.ask) / 2
            long_mid = (long_leg.bid + long_leg.ask) / 2
            credit = short_mid - long_mid
            if credit <= 0:
                continue

            bid_ask_ratio = ((short_leg.ask - short_leg.bid) + (long_leg.ask - long_leg.bid)) / width
            if bid_ask_ratio > MAX_BID_ASK_RATIO:
                continue
            short_delta = short_leg.delta

            candidates.append(
                CandidateSpread(
                    ticker=ticker,
                    strategy="bull_put_credit_spread",
                    short_leg=short_leg,
                    long_leg=long_leg,
                    dte=dte,
                    credit=credit,
                    max_loss=width - credit,
                    width=width,
                    expiry=short_leg.expiry,
                    short_delta=short_delta,
                    pop=1 - abs(short_delta),
                    bid_ask_ratio=bid_ask_ratio,
                )
            )

    return sorted(candidates, key=lambda candidate: (candidate.expiry, -candidate.credit, candidate.width))
