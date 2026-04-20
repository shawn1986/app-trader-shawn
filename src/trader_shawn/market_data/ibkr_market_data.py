from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from importlib import import_module
import math
from typing import Any

from trader_shawn.domain.models import AccountSnapshot, OptionQuote


class IbkrMarketDataClient:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 7,
        client: Any | None = None,
        ib: Any | None = None,
        ibkr_module: Any | None = None,
        ib_api: Any | None = None,
        client_factory: Any | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._client = client if client is not None else ib
        self._ibkr_module = ibkr_module if ibkr_module is not None else ib_api
        self._client_factory = client_factory

    def ensure_connected(self) -> Any:
        client = self._resolve_client()
        is_connected = getattr(client, "isConnected", None)
        if callable(is_connected) and is_connected():
            return client

        connect = getattr(client, "connect", None)
        if not callable(connect):
            raise RuntimeError("IBKR client does not support connect()")
        connect(self._host, self._port, clientId=self._client_id)
        return client

    def fetch_underlying_spot(
        self,
        ticker: str,
        *,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> float:
        client = self.ensure_connected()
        ibkr = self._resolve_ibkr_module()
        stock = ibkr.Stock(symbol=ticker, exchange=exchange, currency=currency)
        qualified_stock = list(client.qualifyContracts(stock))[0]
        snapshot = client.reqTickers(qualified_stock)[0]
        return _ticker_market_price(snapshot)

    def fetch_spot_price(
        self,
        ticker: str,
        *,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> float:
        return self.fetch_underlying_spot(
            ticker,
            exchange=exchange,
            currency=currency,
        )

    def fetch_option_quotes(
        self,
        ticker: str,
        *,
        min_dte: int = 7,
        max_dte: int = 21,
        rights: Iterable[str] = ("P", "C"),
        as_of: date | None = None,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> list[OptionQuote]:
        if min_dte < 0 or max_dte < min_dte:
            raise ValueError("invalid DTE bounds")

        client = self.ensure_connected()
        ibkr = self._resolve_ibkr_module()
        stock = ibkr.Stock(symbol=ticker, exchange=exchange, currency=currency)
        qualified_stock = list(client.qualifyContracts(stock))[0]
        option_chain = _select_option_chain(
            client.reqSecDefOptParams(
                ticker,
                "",
                str(getattr(qualified_stock, "secType", "STK")),
                int(getattr(qualified_stock, "conId", 0)),
            ),
            exchange=exchange,
        )
        option_contracts = [
            ibkr.Option(
                symbol=ticker,
                lastTradeDateOrContractMonth=_ibkr_expiry(expiry),
                strike=float(strike),
                right=right,
                exchange=exchange,
                currency=currency,
            )
            for expiry in _bounded_expiries(
                option_chain.expirations,
                today=as_of or date.today(),
                min_dte=min_dte,
                max_dte=max_dte,
            )
            for strike in sorted(float(value) for value in option_chain.strikes)
            for right in _normalize_rights(rights)
        ]
        if not option_contracts:
            return []

        qualified_options = list(client.qualifyContracts(*option_contracts))
        snapshots = list(client.reqTickers(*qualified_options))
        return self.normalize_option_quotes(
            ticker,
            [_ticker_to_quote_row(snapshot) for snapshot in snapshots],
        )

    def fetch_account_snapshot(self) -> AccountSnapshot:
        rows = list(self.ensure_connected().accountSummary())
        tags = {str(row.tag): row for row in rows}
        return AccountSnapshot(
            account_id=str(rows[0].account) if rows else "",
            buying_power=_summary_float(tags, "BuyingPower"),
            net_liquidation=_summary_float(tags, "NetLiquidation"),
            cash=_summary_float(tags, "TotalCashValue"),
            excess_liquidity=_summary_float(tags, "ExcessLiquidity"),
            realized_pnl=_summary_float(tags, "RealizedPnL"),
            unrealized_pnl=_summary_float(tags, "UnrealizedPnL"),
            updated_at=datetime.now(UTC),
        )

    def count_open_option_positions(self, *, symbol: str | None = None) -> int:
        count = 0
        for position in self.ensure_connected().positions():
            contract = getattr(position, "contract", None)
            if contract is None:
                continue
            if str(getattr(contract, "secType", "")).upper() not in {"OPT", "FOP"}:
                continue
            if symbol is not None and str(getattr(contract, "symbol", "")) != symbol:
                continue
            if float(getattr(position, "position", 0)) == 0:
                continue
            count += 1
        return math.ceil(count / 2)

    def count_open_option_symbols(self) -> int:
        symbols: set[str] = set()
        for position in self.ensure_connected().positions():
            contract = getattr(position, "contract", None)
            if contract is None:
                continue
            if str(getattr(contract, "secType", "")).upper() not in {"OPT", "FOP"}:
                continue
            if float(getattr(position, "position", 0)) == 0:
                continue
            symbols.add(str(getattr(contract, "symbol", "")))
        return len(symbols)

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

    def _resolve_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory()
            return self._client
        self._client = self._resolve_ibkr_module().IB()
        return self._client

    def _resolve_ibkr_module(self) -> Any:
        if self._ibkr_module is None:
            self._ibkr_module = import_module("ib_insync")
        return self._ibkr_module


def _bounded_expiries(
    expirations: Iterable[str],
    *,
    today: date,
    min_dte: int,
    max_dte: int,
) -> list[str]:
    bounded: list[str] = []
    for expiry in sorted(_normalize_expiry(str(value)) for value in expirations):
        dte = (date.fromisoformat(expiry) - today).days
        if min_dte <= dte <= max_dte:
            bounded.append(expiry)
    return bounded


def _normalize_expiry(expiry: str) -> str:
    if len(expiry) == 8 and expiry.isdigit():
        return f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:]}"
    return expiry


def _ibkr_expiry(expiry: str) -> str:
    return _normalize_expiry(expiry).replace("-", "")


def _normalize_rights(rights: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for right in rights:
        value = str(right).upper()
        if value not in {"P", "C"}:
            raise ValueError(f"invalid option right: {right}")
        if value not in normalized:
            normalized.append(value)
    return normalized


def _select_option_chain(chains: Iterable[Any], *, exchange: str) -> Any:
    available = list(chains)
    if not available:
        raise RuntimeError("IBKR returned no option chain definitions")
    for chain in available:
        if str(getattr(chain, "exchange", "")).upper() == exchange.upper():
            return chain
    return available[0]


def _ticker_to_quote_row(snapshot: Any) -> dict[str, object]:
    contract = snapshot.contract
    right = str(contract.right).upper()
    return {
        "expiry": _normalize_expiry(str(contract.lastTradeDateOrContractMonth)),
        "strike": float(contract.strike),
        "right": right,
        "bid": _finite_or_zero(getattr(snapshot, "bid", None)),
        "ask": _finite_or_zero(getattr(snapshot, "ask", None)),
        "delta": _extract_delta(snapshot),
        "last": _optional_float(getattr(snapshot, "last", None)),
        "mark": _ticker_mark(snapshot),
        "volume": _extract_option_metric(snapshot, right, "volume"),
        "open_interest": _extract_option_metric(snapshot, right, "open_interest"),
    }


def _extract_delta(snapshot: Any) -> float | None:
    for field_name in ("bidGreeks", "modelGreeks", "askGreeks", "lastGreeks"):
        greeks = getattr(snapshot, field_name, None)
        if greeks is None:
            continue
        delta = _optional_float(getattr(greeks, "delta", None))
        if delta is not None:
            return delta
    return None


def _extract_option_metric(snapshot: Any, right: str, metric: str) -> int:
    side_name = "put" if right == "P" else "call"
    candidate_names = [
        f"{side_name}OpenInterest" if metric == "open_interest" else f"{side_name}Volume",
        "openInterest" if metric == "open_interest" else "volume",
    ]
    for name in candidate_names:
        value = getattr(snapshot, name, None)
        if value not in (None, ""):
            return int(value)
    return 0


def _ticker_mark(snapshot: Any) -> float | None:
    bid = _optional_float(getattr(snapshot, "bid", None))
    ask = _optional_float(getattr(snapshot, "ask", None))
    if bid is not None and ask is not None:
        return round((bid + ask) / 2, 10)
    return _optional_float(getattr(snapshot, "last", None))


def _ticker_market_price(snapshot: Any) -> float:
    market_price = getattr(snapshot, "marketPrice", None)
    if callable(market_price):
        value = _optional_float(market_price())
        if value is not None and value > 0:
            return value
    for field_name in ("last", "close", "bid", "ask"):
        value = _optional_float(getattr(snapshot, field_name, None))
        if value is not None and value > 0:
            return value
    raise RuntimeError("IBKR returned no finite underlying spot price")


def _summary_float(tags: Mapping[str, Any], tag: str) -> float:
    row = tags.get(tag)
    if row is None:
        return 0.0
    return _finite_or_zero(getattr(row, "value", None))


def _finite_or_zero(value: object) -> float:
    parsed = _optional_float(value)
    return 0.0 if parsed is None else parsed


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        return None
    return parsed


def _optional_int(value: object) -> int:
    if value is None or value == "":
        return 0
    return int(value)
