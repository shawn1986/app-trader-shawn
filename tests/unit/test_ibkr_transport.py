from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import SimpleNamespace

from trader_shawn.domain.models import AccountSnapshot, CandidateSpread, OptionQuote
from trader_shawn.execution.ibkr_executor import IbkrExecutor
from trader_shawn.market_data.ibkr_market_data import IbkrMarketDataClient


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
    def __init__(self) -> None:
        self.connected = False
        self.connect_calls: list[tuple[str, int, int]] = []
        self.qualified_contracts: list[object] = []
        self.ticker_requests: list[list[object]] = []

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
            tickers.append(
                SimpleNamespace(
                    contract=contract,
                    bid=1.1,
                    ask=1.3,
                    last=1.2,
                    close=1.15,
                    bidGreeks=SimpleNamespace(delta=-0.21),
                    modelGreeks=None,
                    putVolume=140,
                    callVolume=75,
                    putOpenInterest=610,
                    callOpenInterest=320,
                )
            )
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
                position=2,
                contract=SimpleNamespace(secType="OPT", symbol="AMD"),
            ),
            SimpleNamespace(
                account="DU123",
                position=0,
                contract=SimpleNamespace(secType="OPT", symbol="AMD"),
            ),
            SimpleNamespace(
                account="DU123",
                position=100,
                contract=SimpleNamespace(secType="STK", symbol="AMD"),
            ),
        ]


class FakeExecutionClient:
    def __init__(self) -> None:
        self.qualified_contracts: list[object] = []
        self.placed_orders: list[tuple[object, object]] = []

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
