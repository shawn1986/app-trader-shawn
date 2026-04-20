from __future__ import annotations

from collections.abc import Iterable, Mapping

from trader_shawn.domain.models import OptionQuote


class IbkrMarketDataClient:
    def normalize_option_quotes(
        self,
        ticker: str,
        raw_quotes: Iterable[Mapping[str, object]],
    ) -> list[OptionQuote]:
        quotes: list[OptionQuote] = []
        for row in raw_quotes:
            quotes.append(
                OptionQuote(
                    symbol=ticker,
                    expiry=str(row["expiry"]),
                    strike=float(row["strike"]),
                    right=str(row["right"]),
                    bid=float(row["bid"]),
                    ask=float(row["ask"]),
                    delta=_optional_float(row.get("delta")),
                    last=_optional_float(row.get("last")),
                    mark=_optional_float(row.get("mark")),
                    volume=int(row.get("volume", 0)),
                    open_interest=int(row.get("open_interest", 0)),
                )
            )
        return quotes


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)
