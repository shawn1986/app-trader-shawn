from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import math
from types import SimpleNamespace

import pytest

from trader_shawn.domain.models import (
    AccountSnapshot,
    BrokerOptionPosition,
    CandidateSpread,
    OptionQuote,
    PositionSnapshot,
)
from trader_shawn.execution.ibkr_executor import IbkrExecutor
from trader_shawn.market_data.ibkr_market_data import (
    IbkrMarketDataClient,
    _extract_delta,
)


@dataclass
class FakeOption:
    symbol: str
    lastTradeDateOrContractMonth: str
    strike: float
    right: str
    exchange: str
    currency: str
    tradingClass: str = ""
    multiplier: str = ""
    conId: int = 0
    secType: str = "OPT"


@dataclass
class FakeStock:
    symbol: str
    exchange: str
    currency: str
    conId: int = 0
    secType: str = "STK"


@dataclass
class FakeComboLeg:
    conId: int
    ratio: int
    action: str
    exchange: str


class FakeContract:
    def __init__(
        self,
        *,
        symbol: str = "",
        secType: str = "",
        currency: str = "",
        exchange: str = "",
        comboLegs: list[FakeComboLeg] | None = None,
    ) -> None:
        self.symbol = symbol
        self.secType = secType
        self.currency = currency
        self.exchange = exchange
        self.comboLegs = comboLegs or []


class FakeLimitOrder:
    def __init__(
        self,
        action: str,
        totalQuantity: int,
        lmtPrice: float,
        *,
        transmit: bool,
    ) -> None:
        self.action = action
        self.totalQuantity = totalQuantity
        self.lmtPrice = lmtPrice
        self.transmit = transmit
        self.orderType = "LMT"
        self.orderId = 321


class FakeIbModule:
    Option = FakeOption
    Stock = FakeStock
    ComboLeg = FakeComboLeg
    Contract = FakeContract
    LimitOrder = FakeLimitOrder


class FakeMarketDataClient:
    def __init__(
        self,
        *,
        quote_overrides: dict[tuple[str, float], dict[str, object]] | None = None,
        expirations: set[str] | None = None,
        strikes: list[float] | None = None,
        spot_price: float = 101.25,
        historical_close: float | None = None,
        valid_option_keys: set[tuple[str, float, str]] | None = None,
    ) -> None:
        self.connected = False
        self.connect_calls: list[tuple[str, int, int]] = []
        self.qualified_contracts: list[object] = []
        self.ticker_requests: list[list[object]] = []
        self.market_data_type_requests: list[int] = []
        self.disconnect_calls = 0
        self.historical_requests: list[object] = []
        self.contract_detail_requests: list[object] = []
        self.quote_overrides = quote_overrides or {}
        self.expirations = expirations or {
            "2026-04-25",
            "2026-04-30",
            "2026-05-08",
            "2026-05-20",
        }
        self.strikes = strikes or [95.0, 100.0]
        self.spot_price = spot_price
        self.historical_close = historical_close
        self.valid_option_keys = valid_option_keys

    def isConnected(self) -> bool:
        return self.connected

    def connect(self, host: str, port: int, clientId: int) -> "FakeMarketDataClient":
        self.connected = True
        self.connect_calls.append((host, port, clientId))
        return self

    def disconnect(self) -> None:
        self.connected = False
        self.disconnect_calls += 1

    def reqMarketDataType(self, market_data_type: int) -> None:
        self.market_data_type_requests.append(market_data_type)

    def qualifyContracts(self, *contracts: object) -> list[object]:
        self.qualified_contracts.extend(contracts)
        for index, contract in enumerate(contracts, start=1):
            if getattr(contract, "secType", "") == "STK":
                contract.conId = 9000
                continue
            expiry = str(getattr(contract, "lastTradeDateOrContractMonth"))
            strike = int(float(getattr(contract, "strike")))
            right = str(getattr(contract, "right"))
            contract.conId = int(expiry.replace("-", "")[-4:]) * 10 + strike + (
                0 if right == "P" else 1
            )
        return list(contracts)

    def reqTickers(self, *contracts: object) -> list[object]:
        self.ticker_requests.append(list(contracts))
        if len(contracts) == 1 and getattr(contracts[0], "secType", "") == "STK":
            return [
                SimpleNamespace(
                    contract=contracts[0],
                    marketPrice=lambda: self.spot_price,
                    last=self.spot_price,
                    close=self.spot_price,
                    bid=self.spot_price - 0.05,
                    ask=self.spot_price + 0.05,
                )
            ]

        tickers: list[object] = []
        for contract in contracts:
            strike = float(getattr(contract, "strike", 0.0))
            right = str(getattr(contract, "right", "")).upper()
            bid = 1.1
            ask = 1.3
            last = 1.2
            if strike == 160.0:
                bid = 1.25
                ask = 1.35
                last = 1.31
            elif strike == 155.0:
                bid = 0.72
                ask = 0.84
                last = 0.78
            override = self.quote_overrides.get((right, strike), {})
            ticker_fields = {
                "contract": contract,
                "bid": bid,
                "ask": ask,
                "last": last,
                "close": 1.15,
                "bidGreeks": SimpleNamespace(delta=-0.21),
                "modelGreeks": None,
                "putVolume": 140,
                "callVolume": 75,
                "putOpenInterest": 610,
                "callOpenInterest": 320,
            }
            ticker_fields.update(override)
            tickers.append(SimpleNamespace(**ticker_fields))
        return tickers

    def reqHistoricalData(self, contract: object, **kwargs: object) -> list[object]:
        self.historical_requests.append(contract)
        if self.historical_close is None:
            return []
        return [SimpleNamespace(close=self.historical_close)]

    def reqContractDetails(self, contract: object) -> list[object]:
        self.contract_detail_requests.append(contract)
        expiry = str(getattr(contract, "lastTradeDateOrContractMonth", ""))
        requested_right = str(getattr(contract, "right", "")).upper()
        rights = [requested_right] if requested_right in {"P", "C"} else ["P", "C"]
        details: list[object] = []
        for strike in self.strikes:
            for right in rights:
                key = (expiry, float(strike), right)
                if self.valid_option_keys is not None and key not in self.valid_option_keys:
                    continue
                details.append(
                    SimpleNamespace(
                        contract=FakeOption(
                            symbol=str(getattr(contract, "symbol", "AMD")),
                            lastTradeDateOrContractMonth=expiry,
                            strike=float(strike),
                            right=right,
                            exchange=str(getattr(contract, "exchange", "SMART")),
                            currency=str(getattr(contract, "currency", "USD")),
                            tradingClass=str(getattr(contract, "tradingClass", "AMD")),
                            multiplier=str(getattr(contract, "multiplier", "100")),
                        )
                    )
                )
        return details

    def reqSecDefOptParams(
        self,
        underlyingSymbol: str,
        futFopExchange: str,
        underlyingSecType: str,
        underlyingConId: int,
    ) -> list[object]:
        assert underlyingSymbol == "AMD"
        assert futFopExchange == ""
        assert underlyingSecType == "STK"
        assert underlyingConId == 9000
        return [
            SimpleNamespace(
                exchange="SMART",
                expirations=self.expirations,
                strikes=self.strikes,
                tradingClass="AMD",
                multiplier="100",
            )
        ]

    def accountSummary(self) -> list[object]:
        return [
            SimpleNamespace(account="DU123", tag="NetLiquidation", value="125000.50"),
            SimpleNamespace(account="DU123", tag="BuyingPower", value="250000.00"),
            SimpleNamespace(account="DU123", tag="TotalCashValue", value="40000.00"),
            SimpleNamespace(account="DU123", tag="ExcessLiquidity", value="180000.00"),
            SimpleNamespace(account="DU123", tag="RealizedPnL", value="1200.25"),
            SimpleNamespace(account="DU123", tag="UnrealizedPnL", value="-85.75"),
        ]

    def positions(self) -> list[object]:
        return [
            SimpleNamespace(
                account="DU123",
                position=-1,
                avgCost=1.05,
                contract=SimpleNamespace(
                    secType="OPT",
                    symbol="AMD",
                    lastTradeDateOrContractMonth="20260430",
                    strike=160.0,
                    right="P",
                    conId=80160,
                ),
            ),
            SimpleNamespace(
                account="DU123",
                position=1,
                avgCost=0.33,
                contract=SimpleNamespace(
                    secType="OPT",
                    symbol="AMD",
                    lastTradeDateOrContractMonth="20260430",
                    strike=155.0,
                    right="P",
                    conId=80155,
                ),
            ),
            SimpleNamespace(
                account="DU123",
                position=100,
                contract=SimpleNamespace(secType="STK", symbol="AMD"),
            ),
        ]


class FakeExecutionClient:
    def __init__(self) -> None:
        self.connected = False
        self.connect_calls: list[tuple[str, int, int]] = []
        self.qualified_contracts: list[object] = []
        self.placed_orders: list[tuple[object, object]] = []
        self.disconnect_calls = 0

    def isConnected(self) -> bool:
        return self.connected

    def connect(self, host: str, port: int, clientId: int) -> "FakeExecutionClient":
        self.connected = True
        self.connect_calls.append((host, port, clientId))
        return self

    def disconnect(self) -> None:
        self.connected = False
        self.disconnect_calls += 1

    def qualifyContracts(self, *contracts: object) -> list[object]:
        self.qualified_contracts.extend(contracts)
        for contract in contracts:
            contract.conId = 70000 + int(float(getattr(contract, "strike")))
        return list(contracts)

    def placeOrder(self, contract: object, order: object) -> object:
        self.placed_orders.append((contract, order))
        return SimpleNamespace(
            contract=contract,
            order=order,
            orderStatus=SimpleNamespace(status="PendingSubmit"),
        )


def test_ibkr_executor_submits_transmitted_open_credit_spread_combo() -> None:
    ib_client = FakeExecutionClient()
    executor = IbkrExecutor(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    result = executor.submit_open_credit_spread(
        CandidateSpread(
            ticker="AMD",
            strategy="bull_put_credit_spread",
            expiry="2026-04-30",
            dte=10,
            short_strike=160,
            long_strike=155,
            width=5,
            credit=1.05,
            max_loss=395,
            short_delta=0.2,
            pop=0.8,
            bid_ask_ratio=0.07,
        ),
        limit_credit=1.05,
        quantity=2,
    )

    assert result == {
        "status": "submitted",
        "broker": "ibkr",
        "order_id": 321,
        "order": {
            "action": "SELL",
            "orderType": "LMT",
            "totalQuantity": 2,
            "lmtPrice": 1.05,
            "transmit": True,
        },
        "contract": {
            "symbol": "AMD",
            "secType": "BAG",
            "currency": "USD",
            "exchange": "SMART",
        },
        "legs": [
            {
                "con_id": 70160,
                "action": "SELL",
                "ratio": 1,
                "exchange": "SMART",
                "right": "P",
                "strike": 160.0,
                "expiry": "2026-04-30",
            },
            {
                "con_id": 70155,
                "action": "BUY",
                "ratio": 1,
                "exchange": "SMART",
                "right": "P",
                "strike": 155.0,
                "expiry": "2026-04-30",
            },
        ],
        "broker_status": "PendingSubmit",
    }
    bag_contract, order = ib_client.placed_orders[0]
    assert [contract.lastTradeDateOrContractMonth for contract in ib_client.qualified_contracts] == [
        "20260430",
        "20260430",
    ]
    assert bag_contract.secType == "BAG"
    assert [combo_leg.conId for combo_leg in bag_contract.comboLegs] == [70160, 70155]
    assert order.transmit is True


def test_ibkr_market_data_client_ensures_connection_before_live_requests() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
        host="127.0.0.1",
        port=7497,
        client_id=17,
    )

    returned = client.ensure_connected()

    assert returned is ib_client
    assert ib_client.connect_calls == [("127.0.0.1", 7497, 17)]


def test_ibkr_market_data_client_sets_blocking_request_timeout() -> None:
    ib_client = FakeMarketDataClient()
    ib_client.RequestTimeout = 0
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
        request_timeout_seconds=12,
    )

    client.ensure_connected()

    assert ib_client.RequestTimeout == 12


def test_ibkr_market_data_client_passes_connect_timeout_when_supported() -> None:
    class TimeoutAwareClient(FakeMarketDataClient):
        def connect(
            self,
            host: str,
            port: int,
            clientId: int,
            *,
            timeout: float,
        ) -> "TimeoutAwareClient":
            self.connected = True
            self.connect_calls.append((host, port, clientId, timeout))
            return self

    ib_client = TimeoutAwareClient()
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
        request_timeout_seconds=9,
    )

    client.ensure_connected()

    assert ib_client.connect_calls == [("127.0.0.1", 4002, 7, 9)]


def test_ibkr_market_data_client_defaults_to_ib_gateway_paper_port() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
        client_id=17,
    )

    client.ensure_connected()

    assert ib_client.connect_calls == [("127.0.0.1", 4002, 17)]


def test_ibkr_market_data_client_requests_delayed_market_data_type() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
        market_data_type="delayed",
    )

    client.fetch_underlying_spot("AMD")

    assert ib_client.market_data_type_requests == [3]


def test_ibkr_market_data_client_skips_underlying_quote_for_delayed_option_scan() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
        market_data_type="delayed",
    )

    client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P",),
        as_of=date(2026, 4, 20),
    )

    assert ib_client.ticker_requests
    assert all(
        getattr(contract, "secType", "") != "STK"
        for request in ib_client.ticker_requests
        for contract in request
    )
    assert ib_client.historical_requests == []


def test_ibkr_market_data_client_uses_historical_close_for_live_option_scan() -> None:
    ib_client = FakeMarketDataClient(
        historical_close=100.0,
        strikes=[50.0, 90.0, 95.0, 100.0, 105.0, 110.0, 150.0],
    )
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    quotes = client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P",),
        as_of=date(2026, 4, 20),
        strike_window_pct=0.10,
    )

    assert ib_client.historical_requests
    assert {quote.strike for quote in quotes} == {90.0, 95.0, 100.0, 105.0, 110.0}


def test_ibkr_market_data_client_disconnects_underlying_client() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
        market_data_type="delayed",
    )

    client.ensure_connected()
    client.disconnect()

    assert ib_client.disconnect_calls == 1
    assert ib_client.isConnected() is False


def test_ibkr_market_data_client_fetches_bounded_option_snapshots_for_runtime_wiring() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    quotes = client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P",),
        as_of=date(2026, 4, 20),
    )

    assert quotes == [
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=95.0,
            right="P",
            bid=1.1,
            ask=1.3,
            delta=-0.21,
            last=1.2,
            mark=1.2,
            volume=140,
            open_interest=610,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-04-30",
            strike=100.0,
            right="P",
            bid=1.1,
            ask=1.3,
            delta=-0.21,
            last=1.2,
            mark=1.2,
            volume=140,
            open_interest=610,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-05-08",
            strike=95.0,
            right="P",
            bid=1.1,
            ask=1.3,
            delta=-0.21,
            last=1.2,
            mark=1.2,
            volume=140,
            open_interest=610,
        ),
        OptionQuote(
            symbol="AMD",
            expiry="2026-05-08",
            strike=100.0,
            right="P",
            bid=1.1,
            ask=1.3,
            delta=-0.21,
            last=1.2,
            mark=1.2,
            volume=140,
            open_interest=610,
        ),
    ]
    assert [contract.lastTradeDateOrContractMonth for contract in ib_client.ticker_requests[-1]] == [
        "20260430",
        "20260430",
        "20260508",
        "20260508",
    ]


def test_ibkr_market_data_client_treats_invalid_option_metrics_as_zero() -> None:
    ib_client = FakeMarketDataClient(
        quote_overrides={
            ("P", 95.0): {
                "putVolume": math.nan,
                "putOpenInterest": "12.5",
            }
        }
    )
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    quotes = client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P",),
        as_of=date(2026, 4, 20),
    )

    assert quotes[0].volume == 0
    assert quotes[0].open_interest == 0


def test_ibkr_market_data_client_uses_generic_metric_fallback_when_side_specific_metric_is_invalid() -> None:
    ib_client = FakeMarketDataClient(
        quote_overrides={
            ("P", 95.0): {
                "putVolume": math.nan,
                "volume": 17,
                "putOpenInterest": "12.5",
                "openInterest": 42,
            }
        }
    )
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    quotes = client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P",),
        as_of=date(2026, 4, 20),
    )

    assert quotes[0].volume == 17
    assert quotes[0].open_interest == 42


def test_ibkr_market_data_client_materializes_rights_iterators_once() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    quotes = client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=(right for right in ("P", "C")),
        as_of=date(2026, 4, 20),
    )

    assert len(quotes) == 8
    assert {quote.right for quote in quotes} == {"P", "C"}
    assert len(ib_client.ticker_requests[-1]) == 8


def test_ibkr_market_data_client_limits_option_scan_strikes_around_spot() -> None:
    ib_client = FakeMarketDataClient(
        spot_price=100.0,
        strikes=[50.0, 75.0, 90.0, 95.0, 100.0, 105.0, 110.0, 125.0, 150.0],
    )
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    quotes = client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P",),
        as_of=date(2026, 4, 20),
        strike_window_pct=0.10,
    )

    assert {quote.strike for quote in quotes} == {90.0, 95.0, 100.0, 105.0, 110.0}
    assert {
        float(getattr(contract, "strike"))
        for contract in ib_client.qualified_contracts
        if getattr(contract, "secType", "") == "OPT"
    } == {90.0, 95.0, 100.0, 105.0, 110.0}


def test_ibkr_market_data_client_keeps_closest_strikes_when_window_is_empty() -> None:
    ib_client = FakeMarketDataClient(
        spot_price=100.0,
        strikes=[10.0, 20.0, 500.0],
    )
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    quotes = client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P",),
        as_of=date(2026, 4, 20),
        strike_window_pct=0.01,
        fallback_strike_count=2,
    )

    assert {quote.strike for quote in quotes} == {20.0, 10.0}


def test_ibkr_market_data_client_caps_scan_strikes_even_inside_window() -> None:
    ib_client = FakeMarketDataClient(
        spot_price=100.0,
        strikes=[90.0, 95.0, 100.0, 105.0, 110.0],
    )
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    quotes = client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P",),
        as_of=date(2026, 4, 20),
        strike_window_pct=0.20,
        fallback_strike_count=3,
    )

    assert {quote.strike for quote in quotes} == {95.0, 100.0, 105.0}


def test_ibkr_market_data_client_uses_bounded_middle_strikes_when_spot_unavailable() -> None:
    ib_client = FakeMarketDataClient(
        spot_price=math.nan,
        strikes=[70.0, 80.0, 90.0, 100.0, 110.0, 120.0, 130.0],
    )
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    quotes = client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P",),
        as_of=date(2026, 4, 20),
        fallback_strike_count=3,
    )

    assert {quote.strike for quote in quotes} == {90.0, 100.0, 110.0}


def test_ibkr_market_data_client_uses_smaller_default_contract_set_when_spot_unavailable() -> None:
    ib_client = FakeMarketDataClient(
        spot_price=math.nan,
        strikes=[float(value) for value in range(10, 31)],
    )
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
        market_data_type="delayed",
    )

    client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P", "C"),
        as_of=date(2026, 4, 20),
    )

    option_contracts = [
        contract
        for contract in ib_client.qualified_contracts
        if getattr(contract, "secType", "") == "OPT"
    ]
    assert len(option_contracts) == 8


def test_ibkr_market_data_client_uses_contract_details_to_skip_invalid_combinations() -> None:
    ib_client = FakeMarketDataClient(
        historical_close=100.0,
        expirations={"2026-04-30", "2026-05-08"},
        strikes=[90.0, 95.0, 100.0, 105.0, 110.0],
        valid_option_keys={
            ("20260430", 95.0, "P"),
            ("20260430", 100.0, "C"),
            ("20260508", 105.0, "P"),
        },
    )
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
        market_data_type="delayed",
    )

    quotes = client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P", "C"),
        as_of=date(2026, 4, 20),
        strike_window_pct=0.15,
        max_expiries=2,
    )

    assert ib_client.contract_detail_requests
    assert {
        (
            quote.expiry.replace("-", ""),
            quote.strike,
            quote.right,
        )
        for quote in quotes
    } == {
        ("20260430", 95.0, "P"),
        ("20260430", 100.0, "C"),
        ("20260508", 105.0, "P"),
    }


def test_ibkr_market_data_client_does_not_swallow_contract_detail_timeout() -> None:
    class TimeoutContractDetailsClient(FakeMarketDataClient):
        def reqContractDetails(self, contract: object) -> list[object]:
            _ = contract
            raise TimeoutError("contract details timed out")

    client = IbkrMarketDataClient(
        client=TimeoutContractDetailsClient(),
        ibkr_module=FakeIbModule(),
        market_data_type="delayed",
        request_timeout_seconds=1,
    )

    with pytest.raises(TimeoutError, match="contract details timed out"):
        client.fetch_option_quotes(
            "AMD",
            min_dte=7,
            max_dte=21,
            rights=("P",),
            as_of=date(2026, 4, 20),
        )


def test_ibkr_market_data_client_limits_default_expiry_scan_count() -> None:
    ib_client = FakeMarketDataClient(
        expirations={
            "2026-04-30",
            "2026-05-01",
            "2026-05-04",
            "2026-05-08",
        },
        strikes=[95.0],
    )
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
        market_data_type="delayed",
    )

    client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P",),
        as_of=date(2026, 4, 20),
    )

    assert [
        request.lastTradeDateOrContractMonth
        for request in ib_client.contract_detail_requests
    ] == ["20260430"]


def test_ibkr_market_data_client_includes_chain_trading_class_and_multiplier() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P",),
        as_of=date(2026, 4, 20),
    )

    option_contracts = [
        contract
        for contract in ib_client.qualified_contracts
        if getattr(contract, "secType", "") == "OPT"
    ]
    assert option_contracts
    assert {contract.tradingClass for contract in option_contracts} == {"AMD"}
    assert {contract.multiplier for contract in option_contracts} == {"100"}


def test_ibkr_market_data_client_drops_quotes_with_missing_bid_or_ask() -> None:
    ib_client = FakeMarketDataClient(
        quote_overrides={
            ("P", 95.0): {
                "bid": None,
                "ask": math.nan,
            }
        }
    )
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    quotes = client.fetch_option_quotes(
        "AMD",
        min_dte=7,
        max_dte=21,
        rights=("P",),
        as_of=date(2026, 4, 20),
    )

    assert len(quotes) == 2
    assert {(quote.expiry, quote.strike) for quote in quotes} == {
        ("2026-04-30", 100.0),
        ("2026-05-08", 100.0),
    }


def test_ibkr_market_data_client_maps_account_summary_and_counts_open_option_positions() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
    )

    snapshot = client.fetch_account_snapshot()

    assert snapshot.account_id == "DU123"
    assert snapshot == AccountSnapshot(
        account_id="DU123",
        buying_power=250000.0,
        net_liquidation=125000.5,
        cash=40000.0,
        excess_liquidity=180000.0,
        realized_pnl=1200.25,
        unrealized_pnl=-85.75,
        updated_at=snapshot.updated_at,
    )
    assert snapshot.updated_at.tzinfo is UTC
    assert client.count_open_option_positions() == 1


def test_ibkr_market_data_client_counts_scaled_spread_quantities_per_symbol() -> None:
    class ScaledPositionClient(FakeMarketDataClient):
        def positions(self) -> list[object]:
            return [
                SimpleNamespace(
                    account="DU123",
                    position=-3,
                    contract=SimpleNamespace(
                        secType="OPT",
                        symbol="AMD",
                        lastTradeDateOrContractMonth="20260430",
                        right="P",
                        strike=160.0,
                        conId=80160,
                    ),
                ),
                SimpleNamespace(
                    account="DU123",
                    position=3,
                    contract=SimpleNamespace(
                        secType="OPT",
                        symbol="AMD",
                        lastTradeDateOrContractMonth="20260430",
                        right="P",
                        strike=155.0,
                        conId=80155,
                    ),
                ),
            ]

    client = IbkrMarketDataClient(
        client=ScaledPositionClient(),
        ibkr_module=FakeIbModule(),
    )

    assert client.count_open_option_positions(symbol="AMD") == 3


def test_ibkr_market_data_client_lists_live_option_positions_for_manage() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(client=ib_client, ibkr_module=FakeIbModule())

    positions = client.fetch_option_positions()

    assert positions == [
        BrokerOptionPosition(
            ticker="AMD",
            expiry="2026-04-30",
            right="P",
            quantity=-1,
            short_strike=160.0,
            long_strike=None,
            average_cost=1.05,
            market_price=1.35,
            broker_position_id="80160",
        ),
        BrokerOptionPosition(
            ticker="AMD",
            expiry="2026-04-30",
            right="P",
            quantity=1,
            short_strike=155.0,
            long_strike=None,
            average_cost=0.33,
            market_price=0.72,
            broker_position_id="80155",
        ),
    ]


def test_ibkr_market_data_client_estimates_spread_debit_from_live_legs() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(client=ib_client, ibkr_module=FakeIbModule())

    debit = client.estimate_spread_debit(
        ticker="AMD",
        expiry="2026-04-30",
        short_strike=160.0,
        long_strike=155.0,
        strategy="bull_put_credit_spread",
    )

    assert debit == 0.63


def test_ibkr_market_data_client_estimate_spread_debit_raises_when_short_close_ask_missing() -> None:
    ib_client = FakeMarketDataClient(
        quote_overrides={("P", 160.0): {"ask": None, "last": math.nan}}
    )
    client = IbkrMarketDataClient(client=ib_client, ibkr_module=FakeIbModule())

    with pytest.raises(RuntimeError, match="short leg close ask"):
        client.estimate_spread_debit(
            ticker="AMD",
            expiry="2026-04-30",
            short_strike=160.0,
            long_strike=155.0,
            strategy="bull_put_credit_spread",
        )


def test_ibkr_market_data_client_estimate_spread_debit_raises_when_long_close_bid_missing_for_bear_call() -> None:
    ib_client = FakeMarketDataClient(
        quote_overrides={("C", 160.0): {"bid": None, "last": math.nan}}
    )
    client = IbkrMarketDataClient(client=ib_client, ibkr_module=FakeIbModule())

    with pytest.raises(RuntimeError, match="long leg close bid"):
        client.estimate_spread_debit(
            ticker="AMD",
            expiry="2026-04-30",
            short_strike=155.0,
            long_strike=160.0,
            strategy="bear_call_credit_spread",
        )


def test_ibkr_executor_submit_limit_combo_returns_broker_fingerprint() -> None:
    position = PositionSnapshot(
        ticker="AMD",
        strategy="bull_put_credit_spread",
        expiry="2026-04-30",
        short_strike=160,
        long_strike=155,
        entry_credit=1.00,
        current_debit=0.63,
        dte=9,
        short_leg_distance_pct=0.08,
        quantity=1,
    )
    executor = IbkrExecutor(client=FakeExecutionClient(), ibkr_module=FakeIbModule())

    result = executor.submit_limit_combo(position, limit_price=0.63)

    assert result["broker_fingerprint"] == "AMD|2026-04-30|P|160.0|155.0|1"


def test_ibkr_executor_defaults_to_ib_gateway_paper_port() -> None:
    executor = IbkrExecutor(client=FakeExecutionClient(), ibkr_module=FakeIbModule(), client_id=23)

    executor.submit_open_credit_spread(
        CandidateSpread(
            ticker="AMD",
            strategy="bull_put_credit_spread",
            expiry="2026-04-30",
            dte=10,
            short_strike=160,
            long_strike=155,
            width=5,
            credit=1.05,
            max_loss=395,
            short_delta=0.2,
            pop=0.8,
            bid_ask_ratio=0.07,
        ),
        limit_credit=1.05,
        quantity=1,
    )

    assert executor._client.connect_calls == [("127.0.0.1", 4002, 23)]


def test_ibkr_executor_disconnects_underlying_client() -> None:
    ib_client = FakeExecutionClient()
    executor = IbkrExecutor(client=ib_client, ibkr_module=FakeIbModule())

    executor._ensure_connected()
    executor.disconnect()

    assert ib_client.disconnect_calls == 1
    assert ib_client.isConnected() is False


def test_extract_delta_falls_back_to_model_greeks_when_bid_delta_missing() -> None:
    snapshot = SimpleNamespace(
        bidGreeks=SimpleNamespace(delta=None),
        modelGreeks=SimpleNamespace(delta=-0.18),
    )

    assert _extract_delta(snapshot) == -0.18
