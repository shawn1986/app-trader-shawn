from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from importlib import import_module
import inspect
import math
from typing import Any, Callable

from trader_shawn.domain.models import (
    AccountSnapshot,
    BrokerOptionPosition,
    OptionQuote,
)


class IbkrMarketDataClient:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 7,
        client: Any | None = None,
        ib: Any | None = None,
        ibkr_module: Any | None = None,
        ib_api: Any | None = None,
        client_factory: Any | None = None,
        market_data_type: str = "live",
        request_timeout_seconds: float = 30,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._client = client if client is not None else ib
        self._ibkr_module = ibkr_module if ibkr_module is not None else ib_api
        self._client_factory = client_factory
        self._market_data_type = market_data_type
        self._request_timeout_seconds = request_timeout_seconds

    @property
    def market_data_type(self) -> str:
        return self._market_data_type

    def ensure_connected(self) -> Any:
        client = self._resolve_client()
        self._apply_request_timeout(client)
        is_connected = getattr(client, "isConnected", None)
        if callable(is_connected) and is_connected():
            self._request_market_data_type(client)
            return client

        connect = getattr(client, "connect", None)
        if not callable(connect):
            raise RuntimeError("IBKR client does not support connect()")
        if _callable_accepts_keyword(connect, "timeout"):
            connect(
                self._host,
                self._port,
                clientId=self._client_id,
                timeout=self._request_timeout_seconds,
            )
        else:
            connect(self._host, self._port, clientId=self._client_id)
        self._request_market_data_type(client)
        return client

    def disconnect(self) -> None:
        client = self._client
        if client is None:
            return
        disconnect = getattr(client, "disconnect", None)
        if callable(disconnect):
            disconnect()

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
        strike_window_pct: float = 0.15,
        fallback_strike_count: int | None = None,
        max_expiries: int | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[OptionQuote]:
        fallback_strike_count = (
            4
            if fallback_strike_count is None and self._market_data_type == "delayed"
            else (24 if fallback_strike_count is None else fallback_strike_count)
        )
        max_expiries = (
            1
            if max_expiries is None and self._market_data_type == "delayed"
            else (2 if max_expiries is None else max_expiries)
        )
        if min_dte < 0 or max_dte < min_dte:
            raise ValueError("invalid DTE bounds")
        if strike_window_pct <= 0:
            raise ValueError("strike_window_pct must be positive")
        if fallback_strike_count <= 0:
            raise ValueError("fallback_strike_count must be positive")
        if max_expiries <= 0:
            raise ValueError("max_expiries must be positive")

        _emit_progress(
            progress_callback,
            "ibkr_connecting",
            f"Connecting to IBKR for {ticker}.",
        )
        client = self.ensure_connected()
        ibkr = self._resolve_ibkr_module()
        stock = ibkr.Stock(symbol=ticker, exchange=exchange, currency=currency)
        _emit_progress(
            progress_callback,
            "ibkr_underlying_qualifying",
            f"Qualifying {ticker} stock contract.",
        )
        qualified_stock = list(client.qualifyContracts(stock))[0]
        underlying_spot = None
        if self._market_data_type != "delayed":
            _emit_progress(
                progress_callback,
                "ibkr_underlying_history_fetching",
                f"Fetching {ticker} historical close.",
            )
            underlying_spot = _historical_close_price(
                self._fetch_underlying_history(client, qualified_stock)
            )
        if underlying_spot is None and self._market_data_type != "delayed":
            _emit_progress(
                progress_callback,
                "ibkr_underlying_snapshot_fetching",
                f"Fetching {ticker} underlying snapshot.",
            )
            underlying_snapshot = client.reqTickers(qualified_stock)[0]
            try:
                underlying_spot = _ticker_market_price(underlying_snapshot)
            except RuntimeError:
                underlying_spot = None
        _emit_progress(
            progress_callback,
            "ibkr_option_chain_fetching",
            f"Fetching {ticker} option chain.",
        )
        option_chain = _select_option_chain(
            client.reqSecDefOptParams(
                ticker,
                "",
                str(getattr(qualified_stock, "secType", "STK")),
                int(getattr(qualified_stock, "conId", 0)),
            ),
            exchange=exchange,
        )
        normalized_rights = _normalize_rights(rights)
        if underlying_spot is None:
            selected_strikes = _scan_strikes_without_spot(
                option_chain.strikes,
                fallback_count=fallback_strike_count,
            )
        else:
            selected_strikes = _scan_strikes_near_spot(
                option_chain.strikes,
                spot=underlying_spot,
                window_pct=strike_window_pct,
                fallback_count=fallback_strike_count,
            )
        bounded_expiries = _bounded_expiries(
                option_chain.expirations,
                today=as_of or date.today(),
                min_dte=min_dte,
            max_dte=max_dte,
        )[:max_expiries]
        _emit_progress(
            progress_callback,
            "ibkr_contract_details_fetching",
            f"Fetching {ticker} option contract details.",
        )
        option_contracts = self._fetch_valid_option_contracts(
            client,
            ibkr,
            ticker=ticker,
            expiries=bounded_expiries,
            strikes=selected_strikes,
            rights=normalized_rights,
            exchange=exchange,
            currency=currency,
            option_chain=option_chain,
        )
        if not option_contracts:
            return []

        _emit_progress(
            progress_callback,
            "ibkr_options_qualifying",
            f"Qualifying {ticker} option contracts.",
            contract_count=len(option_contracts),
        )
        qualified_options = list(client.qualifyContracts(*option_contracts))
        _emit_progress(
            progress_callback,
            "ibkr_option_snapshots_fetching",
            f"Requesting {ticker} option quote snapshots.",
            contract_count=len(qualified_options),
        )
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

    def fetch_option_positions(self) -> list[BrokerOptionPosition]:
        client = self.ensure_connected()
        option_positions = [
            position
            for position in client.positions()
            if _is_live_option_position(position)
        ]
        if not option_positions:
            return []

        snapshots = list(
            client.reqTickers(
                *[getattr(position, "contract") for position in option_positions]
            )
        )
        return [
            BrokerOptionPosition(
                ticker=str(getattr(contract, "symbol", "")),
                expiry=_normalize_expiry(
                    str(getattr(contract, "lastTradeDateOrContractMonth", ""))
                ),
                right=str(getattr(contract, "right", "")),
                quantity=int(float(getattr(position, "position", 0))),
                short_strike=float(getattr(contract, "strike", 0.0)),
                long_strike=None,
                average_cost=_optional_float(getattr(position, "avgCost", None)),
                market_price=_position_market_price(
                    snapshot,
                    quantity=float(getattr(position, "position", 0)),
                ),
                broker_position_id=_broker_position_id(contract),
            )
            for position, snapshot in zip(option_positions, snapshots, strict=True)
            for contract in [getattr(position, "contract")]
        ]

    def estimate_spread_debit(
        self,
        *,
        ticker: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        strategy: str,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> float:
        quote_rows = self._fetch_contract_quote_rows(
            ticker=ticker,
            contracts=[
                self._resolve_ibkr_module().Option(
                    symbol=ticker,
                    lastTradeDateOrContractMonth=_ibkr_expiry(expiry),
                    strike=short_strike,
                    right=_option_right_for_strategy(strategy),
                    exchange=exchange,
                    currency=currency,
                ),
                self._resolve_ibkr_module().Option(
                    symbol=ticker,
                    lastTradeDateOrContractMonth=_ibkr_expiry(expiry),
                    strike=long_strike,
                    right=_option_right_for_strategy(strategy),
                    exchange=exchange,
                    currency=currency,
                ),
            ],
        )
        quotes_by_strike = {float(row["strike"]): row for row in quote_rows}
        try:
            short_leg = quotes_by_strike[float(short_strike)]
            long_leg = quotes_by_strike[float(long_strike)]
        except KeyError as exc:
            raise RuntimeError("IBKR returned incomplete spread leg quotes") from exc
        short_close_ask = _require_quote_price(
            short_leg.get("ask"),
            leg_name="short leg",
            price_name="close ask",
        )
        long_close_bid = _require_quote_price(
            long_leg.get("bid"),
            leg_name="long leg",
            price_name="close bid",
        )
        return round(short_close_ask - long_close_bid, 2)

    def count_open_option_positions(self, *, symbol: str | None = None) -> int:
        count = 0.0
        for position in self.ensure_connected().positions():
            contract = getattr(position, "contract", None)
            if contract is None:
                continue
            if str(getattr(contract, "secType", "")).upper() not in {"OPT", "FOP"}:
                continue
            if symbol is not None and str(getattr(contract, "symbol", "")) != symbol:
                continue
            quantity = float(getattr(position, "position", 0))
            if quantity == 0:
                continue
            count += abs(quantity)
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
            bid = _optional_float(row["bid"])
            ask = _optional_float(row["ask"])
            if bid is None or ask is None:
                continue
            quotes.append(
                OptionQuote(
                    symbol=ticker,
                    expiry=str(row["expiry"]),
                    strike=float(row["strike"]),
                    right=str(row["right"]),
                    bid=bid,
                    ask=ask,
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

    def _request_market_data_type(self, client: Any) -> None:
        req_market_data_type = getattr(client, "reqMarketDataType", None)
        if not callable(req_market_data_type):
            return
        if self._market_data_type == "delayed":
            req_market_data_type(3)

    def _fetch_contract_quote_rows(
        self,
        *,
        ticker: str,
        contracts: Iterable[Any],
    ) -> list[dict[str, object]]:
        client = self.ensure_connected()
        qualified_contracts = list(client.qualifyContracts(*list(contracts)))
        snapshots = list(client.reqTickers(*qualified_contracts))
        return [_ticker_to_quote_row(snapshot) for snapshot in snapshots]

    def _fetch_underlying_history(self, client: Any, qualified_stock: Any) -> list[Any]:
        req_historical_data = getattr(client, "reqHistoricalData", None)
        if not callable(req_historical_data):
            return []
        try:
            return list(
                req_historical_data(
                    qualified_stock,
                    endDateTime="",
                    durationStr="2 D",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                    formatDate=1,
                    timeout=self._request_timeout_seconds,
                )
            )
        except Exception:
            return []

    def _fetch_valid_option_contracts(
        self,
        client: Any,
        ibkr: Any,
        *,
        ticker: str,
        expiries: Iterable[str],
        strikes: Iterable[float],
        rights: Iterable[str],
        exchange: str,
        currency: str,
        option_chain: Any,
    ) -> list[Any]:
        selected_strikes = {float(strike) for strike in strikes}
        selected_rights = {str(right).upper() for right in rights}
        expiries_list = list(expiries)
        if not expiries_list or not selected_strikes or not selected_rights:
            return []

        req_contract_details = getattr(client, "reqContractDetails", None)
        if callable(req_contract_details):
            contracts_by_key: dict[tuple[str, float, str], Any] = {}
            for expiry in expiries_list:
                template = ibkr.Option(
                    symbol=ticker,
                    lastTradeDateOrContractMonth=_ibkr_expiry(expiry),
                    strike=0.0,
                    right="",
                    exchange=exchange,
                    currency=currency,
                    tradingClass=str(getattr(option_chain, "tradingClass", "")),
                    multiplier=str(getattr(option_chain, "multiplier", "")),
                )
                try:
                    contract_details = list(req_contract_details(template))
                except Exception as exc:
                    if _is_timeout_error(exc):
                        raise
                    contract_details = []
                for detail in contract_details:
                    contract = getattr(detail, "contract", detail)
                    contract_expiry = _normalize_expiry(
                        str(getattr(contract, "lastTradeDateOrContractMonth", ""))
                    )
                    contract_strike = _optional_float(getattr(contract, "strike", None))
                    contract_right = str(getattr(contract, "right", "")).upper()
                    if (
                        contract_expiry == _normalize_expiry(expiry)
                        and contract_strike in selected_strikes
                        and contract_right in selected_rights
                    ):
                        contracts_by_key[
                            (contract_expiry, contract_strike, contract_right)
                        ] = contract
            return [
                contracts_by_key[key]
                for key in sorted(contracts_by_key, key=lambda item: (item[0], item[1], item[2]))
            ]

        return [
            ibkr.Option(
                symbol=ticker,
                lastTradeDateOrContractMonth=_ibkr_expiry(expiry),
                strike=float(strike),
                right=right,
                exchange=exchange,
                currency=currency,
                tradingClass=str(getattr(option_chain, "tradingClass", "")),
                multiplier=str(getattr(option_chain, "multiplier", "")),
            )
            for expiry in expiries_list
            for strike in selected_strikes
            for right in selected_rights
        ]

    def _apply_request_timeout(self, client: Any) -> None:
        if hasattr(client, "RequestTimeout"):
            setattr(client, "RequestTimeout", self._request_timeout_seconds)


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


def _scan_strikes_near_spot(
    strikes: Iterable[object],
    *,
    spot: float,
    window_pct: float,
    fallback_count: int,
) -> list[float]:
    normalized = sorted(float(value) for value in strikes)
    if not normalized:
        return []

    lower_bound = spot * (1 - window_pct)
    upper_bound = spot * (1 + window_pct)
    bounded = [
        strike
        for strike in normalized
        if lower_bound <= strike <= upper_bound
    ]
    if bounded:
        return sorted(
            sorted(bounded, key=lambda strike: abs(strike - spot))[:fallback_count]
        )

    return sorted(
        sorted(normalized, key=lambda strike: abs(strike - spot))[:fallback_count]
    )


def _scan_strikes_without_spot(
    strikes: Iterable[object],
    *,
    fallback_count: int,
) -> list[float]:
    normalized = sorted(float(value) for value in strikes)
    if len(normalized) <= fallback_count:
        return normalized

    start = max((len(normalized) - fallback_count) // 2, 0)
    return normalized[start:start + fallback_count]


def _historical_close_price(bars: Iterable[Any]) -> float | None:
    for bar in reversed(list(bars)):
        close = _optional_float(getattr(bar, "close", None))
        if close is not None:
            return close
    return None


def _select_option_chain(chains: Iterable[Any], *, exchange: str) -> Any:
    available = list(chains)
    if not available:
        raise RuntimeError("IBKR returned no option chain definitions")
    for chain in available:
        if str(getattr(chain, "exchange", "")).upper() == exchange.upper():
            return chain
    return available[0]


def _option_right_for_strategy(strategy: str) -> str:
    normalized = strategy.lower()
    if normalized == "bull_put_credit_spread":
        return "P"
    if normalized == "bear_call_credit_spread":
        return "C"
    raise ValueError(f"unsupported credit spread strategy: {strategy}")


def _ticker_to_quote_row(snapshot: Any) -> dict[str, object]:
    contract = snapshot.contract
    right = str(contract.right).upper()
    return {
        "expiry": _normalize_expiry(str(contract.lastTradeDateOrContractMonth)),
        "strike": float(contract.strike),
        "right": right,
        "bid": _optional_float(getattr(snapshot, "bid", None)),
        "ask": _optional_float(getattr(snapshot, "ask", None)),
        "delta": _extract_delta(snapshot),
        "last": _optional_float(getattr(snapshot, "last", None)),
        "mark": _ticker_mark(snapshot),
        "volume": _extract_option_metric(snapshot, right, "volume"),
        "open_interest": _extract_option_metric(snapshot, right, "open_interest"),
    }


def _require_quote_price(
    value: object,
    *,
    leg_name: str,
    price_name: str,
) -> float:
    parsed = _optional_float(value)
    if parsed is None:
        raise RuntimeError(f"IBKR returned no {leg_name} {price_name}")
    return parsed


def _is_live_option_position(position: Any) -> bool:
    contract = getattr(position, "contract", None)
    if contract is None:
        return False
    if str(getattr(contract, "secType", "")).upper() not in {"OPT", "FOP"}:
        return False
    return float(getattr(position, "position", 0)) != 0


def _position_market_price(snapshot: Any, *, quantity: float) -> float | None:
    if quantity < 0:
        return _optional_float(getattr(snapshot, "ask", None))
    if quantity > 0:
        return _optional_float(getattr(snapshot, "bid", None))
    return _ticker_mark(snapshot)


def _broker_position_id(contract: Any) -> str | None:
    con_id = getattr(contract, "conId", None)
    if con_id not in (None, "", 0):
        return str(con_id)
    local_symbol = getattr(contract, "localSymbol", None)
    if local_symbol not in (None, ""):
        return str(local_symbol)
    return None


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
            parsed = _try_optional_int(value)
            if parsed is not None:
                return parsed
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
    parsed = _try_optional_int(value)
    return 0 if parsed is None else parsed


def _try_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not parsed.is_integer():
        return None
    return int(parsed)


def _emit_progress(
    progress_callback: Callable[[dict[str, Any]], None] | None,
    stage: str,
    message: str,
    **details: Any,
) -> None:
    if callable(progress_callback):
        progress_callback({"stage": stage, "message": message, **details})


def _callable_accepts_keyword(func: Callable[..., Any], name: str) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == name:
            return True
    return False


def _is_timeout_error(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
