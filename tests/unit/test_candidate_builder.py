from trader_shawn.candidate_builder.credit_spread_builder import build_candidates
from trader_shawn.domain.models import OptionQuote


def test_build_candidates_filters_liquidity_and_creates_bull_put_spread() -> None:
    quotes = [
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=160,
            right="P",
            bid=1.40,
            ask=1.50,
            delta=-0.22,
            open_interest=500,
            volume=120,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=155,
            right="P",
            bid=0.65,
            ask=0.75,
            delta=-0.12,
            open_interest=500,
            volume=100,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=165,
            right="P",
            bid=2.10,
            ask=2.60,
            delta=-0.31,
            open_interest=5,
            volume=1,
        ),
    ]

    candidates = build_candidates("AMD", 10, quotes)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.strategy == "bull_put_credit_spread"
    assert candidate.short_strike == 160
    assert candidate.long_strike == 155
    assert round(candidate.credit, 2) == 0.75
