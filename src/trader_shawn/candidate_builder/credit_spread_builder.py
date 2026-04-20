from __future__ import annotations

from trader_shawn.domain.models import CandidateSpread, OptionQuote

MIN_OPEN_INTEREST = 100
MIN_VOLUME = 50
MAX_BID_ASK_SPREAD = 0.25


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
            and quote.open_interest >= MIN_OPEN_INTEREST
            and quote.volume >= MIN_VOLUME
            and (quote.ask - quote.bid) <= MAX_BID_ASK_SPREAD
        ),
        key=lambda quote: (-quote.strike, quote.expiry),
    )

    candidates: list[CandidateSpread] = []
    for short_leg in eligible_quotes:
        for long_leg in eligible_quotes:
            width = short_leg.strike - long_leg.strike
            if short_leg.expiry != long_leg.expiry or width <= 0 or width > dte:
                continue

            credit = short_leg.bid - long_leg.bid
            if credit <= 0:
                continue

            short_ratio = short_leg.bid / short_leg.ask if short_leg.ask > 0 else 0.0
            long_ratio = long_leg.bid / long_leg.ask if long_leg.ask > 0 else 0.0
            bid_ask_ratio = min(short_ratio, long_ratio)
            short_delta = short_leg.delta or 0.0

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

    return sorted(candidates, key=lambda candidate: (-candidate.credit, candidate.width))
