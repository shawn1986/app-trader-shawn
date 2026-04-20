from datetime import date

from trader_shawn.candidate_builder.credit_spread_builder import build_candidates
from trader_shawn.events.earnings_calendar import EarningsCalendar
from trader_shawn.market_data.ibkr_market_data import IbkrMarketDataClient
from trader_shawn.domain.models import OptionQuote


def test_build_candidates_applies_task3_filters_and_builds_bull_put_spread() -> None:
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
            delta=-0.18,
            open_interest=500,
            volume=100,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=165,
            right="P",
            bid=2.10,
            ask=2.40,
            delta=-0.20,
            open_interest=5,
            volume=1,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=150,
            right="P",
            bid=0.30,
            ask=1.10,
            delta=-0.16,
            open_interest=500,
            volume=100,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=145,
            right="P",
            bid=0.20,
            ask=0.30,
            delta=None,
            open_interest=500,
            volume=100,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=170,
            right="C",
            bid=0.90,
            ask=1.05,
            delta=0.21,
            open_interest=500,
            volume=100,
        ),
    ]

    candidates = build_candidates("AMD", 10, quotes)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.ticker == "AMD"
    assert candidate.strategy == "bull_put_credit_spread"
    assert candidate.expiry == "2026-04-30"
    assert candidate.dte == 10
    assert candidate.short_strike == 160
    assert candidate.long_strike == 155
    assert candidate.width == 5
    assert round(candidate.credit, 2) == 0.75
    assert round(candidate.max_loss, 2) == 4.25
    assert round(candidate.short_delta, 2) == -0.22
    assert round(candidate.pop, 2) == 0.78
    assert round(candidate.bid_ask_ratio, 2) == 0.04


def test_earnings_calendar_detects_blocking_event_in_window() -> None:
    calendar = EarningsCalendar(
        [
            {"ticker": "AMD", "date": date(2026, 4, 28)},
            {"ticker": "NVDA", "date": date(2026, 4, 28)},
            {"ticker": "AMD", "date": date(2026, 5, 10)},
        ]
    )

    assert calendar.has_blocking_event("AMD", date(2026, 4, 25), date(2026, 4, 30)) is True
    assert calendar.has_blocking_event("AMD", date(2026, 5, 1), date(2026, 5, 5)) is False


def test_ibkr_market_data_client_normalizes_rows_with_passed_ticker() -> None:
    client = IbkrMarketDataClient()

    quotes = client.normalize_option_quotes(
        "AMD",
        [
            {
                "expiry": "2026-04-30",
                "strike": "160",
                "right": "P",
                "bid": "1.40",
                "ask": "1.60",
                "delta": "-0.22",
                "volume": "120",
                "open_interest": "500",
            }
        ],
    )

    assert quotes == [
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=160.0,
            right="P",
            bid=1.40,
            ask=1.60,
            delta=-0.22,
            volume=120,
            open_interest=500,
        )
    ]
