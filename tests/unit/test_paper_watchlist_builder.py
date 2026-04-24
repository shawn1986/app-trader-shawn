from trader_shawn.candidate_builder.paper_watchlist_builder import build_paper_watchlist
from trader_shawn.domain.models import OptionQuote, PaperWatchlistEntry
from trader_shawn.events.earnings_calendar import EarningsCalendar


def test_build_paper_watchlist_pairs_adjacent_puts_even_when_live_filters_fail() -> None:
    quotes = [
        OptionQuote(
            symbol="AMD",
            expiry="2026-05-01",
            strike=160,
            right="P",
            bid=-1.0,
            ask=-1.0,
            delta=None,
            open_interest=0,
            volume=0,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-05-01",
            strike=155,
            right="P",
            bid=-1.0,
            ask=-1.0,
            delta=-0.07,
            open_interest=0,
            volume=0,
        ),
    ]

    watchlist = build_paper_watchlist("AMD", 7, quotes)

    assert watchlist == [
        PaperWatchlistEntry(
            ticker="AMD",
            strategy="bull_put_credit_spread",
            expiry="2026-05-01",
            dte=7,
            short_strike=160,
            long_strike=155,
            width=5,
            short_delta=None,
            flags=[
                "missing_delta",
                "low_open_interest",
                "low_volume",
                "missing_market_prices",
            ],
        )
    ]


def test_build_paper_watchlist_prefers_tighter_widths_before_wider_pairs() -> None:
    quotes = [
        OptionQuote(
            symbol="AMD",
            expiry="2026-05-01",
            strike=160,
            right="P",
            bid=-1.0,
            ask=-1.0,
            delta=-0.30,
            open_interest=0,
            volume=0,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-05-01",
            strike=157.5,
            right="P",
            bid=-1.0,
            ask=-1.0,
            delta=-0.12,
            open_interest=0,
            volume=0,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-05-01",
            strike=155,
            right="P",
            bid=-1.0,
            ask=-1.0,
            delta=-0.05,
            open_interest=0,
            volume=0,
        ),
    ]

    watchlist = build_paper_watchlist("AMD", 7, quotes)

    assert [entry.width for entry in watchlist] == [2.5, 5.0]
    assert [entry.short_strike for entry in watchlist] == [160, 160]


def test_build_paper_watchlist_marks_blocking_event_when_expiry_is_earnings_blocked() -> None:
    quotes = [
        OptionQuote(
            symbol="AMD",
            expiry="2026-05-01",
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
            expiry="2026-05-01",
            strike=155,
            right="P",
            bid=0.70,
            ask=0.75,
            delta=-0.10,
            open_interest=500,
            volume=120,
        ),
    ]

    watchlist = build_paper_watchlist(
        "AMD",
        7,
        quotes,
        earnings_calendar=EarningsCalendar([{"ticker": "AMD", "date": "2026-04-30"}]),
        as_of="2026-04-24",
    )

    assert watchlist == [
        PaperWatchlistEntry(
            ticker="AMD",
            strategy="bull_put_credit_spread",
            expiry="2026-05-01",
            dte=7,
            short_strike=160,
            long_strike=155,
            width=5,
            short_delta=-0.20,
            flags=["blocked_by_event"],
        )
    ]
