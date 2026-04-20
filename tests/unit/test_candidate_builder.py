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
            bid=1.45,
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
            bid=0.70,
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
    assert round(candidate.short_delta, 2) == 0.22
    assert round(candidate.pop, 2) == 0.78
    assert round(candidate.bid_ask_ratio, 2) == 0.13


def test_build_candidates_generates_bull_put_and_bear_call_credit_spreads() -> None:
    quotes = [
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=160,
            right="P",
            bid=1.45,
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
            bid=0.70,
            ask=0.75,
            delta=-0.10,
            open_interest=10,
            volume=1,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=170,
            right="C",
            bid=1.10,
            ask=1.15,
            delta=0.21,
            open_interest=500,
            volume=100,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=175,
            right="C",
            bid=0.35,
            ask=0.40,
            delta=0.10,
            open_interest=10,
            volume=1,
        ),
    ]

    candidates = build_candidates("AMD", 10, quotes)

    assert len(candidates) == 2
    candidates_by_strategy = {
        candidate.strategy: candidate for candidate in candidates
    }

    bull_put = candidates_by_strategy["bull_put_credit_spread"]
    assert bull_put.short_strike == 160
    assert bull_put.long_strike == 155
    assert round(bull_put.credit, 2) == 0.75

    bear_call = candidates_by_strategy["bear_call_credit_spread"]
    assert bear_call.short_strike == 170
    assert bear_call.long_strike == 175
    assert round(bear_call.credit, 2) == 0.75


def test_build_candidates_blocks_entries_when_earnings_falls_before_expiry() -> None:
    quotes = [
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=160,
            right="P",
            bid=1.45,
            ask=1.50,
            delta=-0.20,
            open_interest=500,
            volume=120,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=155,
            right="P",
            bid=0.70,
            ask=0.75,
            delta=-0.10,
            open_interest=10,
            volume=1,
        ),
    ]
    calendar = EarningsCalendar([{"ticker": "AMD", "date": date(2026, 4, 28)}])

    candidates = build_candidates(
        "AMD",
        10,
        quotes,
        earnings_calendar=calendar,
        as_of=date(2026, 4, 20),
    )

    assert candidates == []


def test_build_candidates_allows_long_leg_outside_short_delta_band() -> None:
    quotes = [
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=160,
            right="P",
            bid=1.45,
            ask=1.50,
            delta=-0.20,
            open_interest=500,
            volume=120,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=155,
            right="P",
            bid=0.70,
            ask=0.75,
            delta=-0.08,
            open_interest=10,
            volume=1,
        ),
    ]

    candidates = build_candidates("AMD", 10, quotes)

    assert len(candidates) == 1
    assert candidates[0].short_strike == 160
    assert candidates[0].long_strike == 155


def test_build_candidates_accepts_boundary_short_delta_values() -> None:
    quotes = [
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=160,
            right="P",
            bid=1.45,
            ask=1.50,
            delta=-0.25,
            open_interest=500,
            volume=120,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=155,
            right="P",
            bid=0.70,
            ask=0.75,
            delta=-0.10,
            open_interest=10,
            volume=1,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=150,
            right="P",
            bid=0.69,
            ask=0.70,
            delta=-0.15,
            open_interest=500,
            volume=120,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=145,
            right="P",
            bid=0.45,
            ask=0.46,
            delta=-0.05,
            open_interest=10,
            volume=1,
        ),
    ]

    candidates = build_candidates("AMD", 10, quotes)

    assert [candidate.short_strike for candidate in candidates] == [160, 150]
    assert [round(candidate.short_delta, 2) for candidate in candidates] == [0.25, 0.15]


def test_build_candidates_rejects_expiry_mismatch() -> None:
    quotes = [
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=160,
            right="P",
            bid=1.40,
            ask=1.50,
            delta=-0.20,
            open_interest=500,
            volume=120,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-05-07",
            strike=155,
            right="P",
            bid=0.65,
            ask=0.75,
            delta=-0.10,
            open_interest=10,
            volume=1,
        ),
    ]

    assert build_candidates("AMD", 10, quotes) == []


def test_build_candidates_rejects_width_above_five() -> None:
    quotes = [
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=160,
            right="P",
            bid=1.40,
            ask=1.50,
            delta=-0.20,
            open_interest=500,
            volume=120,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=154,
            right="P",
            bid=0.65,
            ask=0.75,
            delta=-0.10,
            open_interest=10,
            volume=1,
        ),
    ]

    assert build_candidates("AMD", 10, quotes) == []


def test_build_candidates_rejects_bid_ask_ratio_above_threshold() -> None:
    quotes = [
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=160,
            right="P",
            bid=1.40,
            ask=1.80,
            delta=-0.20,
            open_interest=500,
            volume=120,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=155,
            right="P",
            bid=0.65,
            ask=1.05,
            delta=-0.10,
            open_interest=10,
            volume=1,
        ),
    ]

    assert build_candidates("AMD", 10, quotes) == []


def test_build_candidates_rejects_low_credit_spread_with_wide_market() -> None:
    quotes = [
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=160,
            right="P",
            bid=1.00,
            ask=1.05,
            delta=-0.20,
            open_interest=500,
            volume=120,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=155,
            right="P",
            bid=0.80,
            ask=0.85,
            delta=-0.10,
            open_interest=10,
            volume=1,
        ),
    ]

    assert build_candidates("AMD", 10, quotes) == []


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


def test_earnings_calendar_accepts_iso_date_strings() -> None:
    calendar = EarningsCalendar(
        [
            {"ticker": "AMD", "date": "2026-04-28"},
            {"ticker": "AMD", "date": "2026-05-10"},
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


def test_ibkr_market_data_client_defaults_missing_volume_and_open_interest_to_zero() -> None:
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
                "volume": None,
                "open_interest": "",
            }
        ],
    )

    assert quotes[0].volume == 0
    assert quotes[0].open_interest == 0
