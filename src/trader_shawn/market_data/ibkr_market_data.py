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
                    volume=_optional_int(row.get("volume")),
                    open_interest=_optional_int(row.get("open_interest")),
                )
            )
        return quotes


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: object) -> int:
    if value is None or value == "":
        return 0
    return int(value)
