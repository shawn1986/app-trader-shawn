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
    ) -> None:
        self.connected = False
        self.connect_calls: list[tuple[str, int, int]] = []
        self.qualified_contracts: list[object] = []
        self.ticker_requests: list[list[object]] = []
        self.quote_overrides = quote_overrides or {}

    def isConnected(self) -> bool:
        return self.connected

    def connect(self, host: str, port: int, clientId: int) -> "FakeMarketDataClient":
        self.connected = True
        self.connect_calls.append((host, port, clientId))
        return self

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
                    marketPrice=lambda: 101.25,
                    last=101.25,
                    close=100.9,
                    bid=101.2,
                    ask=101.3,
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
                expirations={
                    "2026-04-25",
                    "2026-04-30",
                    "2026-05-08",
                    "2026-05-20",
                },
                strikes=[95.0, 100.0],
                tradingClass="AMD",
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

    def isConnected(self) -> bool:
        return self.connected

    def connect(self, host: str, port: int, clientId: int) -> "FakeExecutionClient":
        self.connected = True
        self.connect_calls.append((host, port, clientId))
        return self

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


def test_ibkr_market_data_client_defaults_to_ib_gateway_paper_port() -> None:
    ib_client = FakeMarketDataClient()
    client = IbkrMarketDataClient(
        client=ib_client,
        ibkr_module=FakeIbModule(),
        client_id=17,
    )

    client.ensure_connected()

    assert ib_client.connect_calls == [("127.0.0.1", 4002, 17)]


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


def test_extract_delta_falls_back_to_model_greeks_when_bid_delta_missing() -> None:
    snapshot = SimpleNamespace(
        bidGreeks=SimpleNamespace(delta=None),
        modelGreeks=SimpleNamespace(delta=-0.18),
    )

    assert _extract_delta(snapshot) == -0.18
