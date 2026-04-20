from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from trader_shawn.domain.enums import DecisionAction, OptionRight, PositionSide


@dataclass(slots=True)
class OptionQuote:
    symbol: str
    expiry: str
    strike: float
    right: str
    bid: float
    ask: float
    delta: float | None = None
    last: float | None = None
    mark: float | None = None
    volume: int = 0
    open_interest: int = 0

    def __post_init__(self) -> None:
        self.right = self.right.upper()
        if self.right not in {"C", "P"}:
            try:
                option_right = OptionRight(self.right.lower())
            except ValueError as exc:
                raise ValueError(f"invalid option right: {self.right}") from exc
            self.right = "C" if option_right is OptionRight.CALL else "P"

    @property
    def ticker(self) -> str:
        return self.symbol


@dataclass(slots=True)
class CandidateSpread:
    ticker: str
    strategy: str
    short_leg: OptionQuote | None = None
    long_leg: OptionQuote | None = None
    dte: int = 0
    credit: float = 0.0
    max_loss: float = 0.0
    width: float = 0.0
    expiry: str = ""
    short_delta: float = 0.0
    pop: float = 0.0
    bid_ask_ratio: float = 0.0
    short_strike: float | None = None
    long_strike: float | None = None

    def __post_init__(self) -> None:
        if self.short_leg is not None:
            self.short_strike = self.short_leg.strike
        if self.long_leg is not None:
            self.long_strike = self.long_leg.strike

        if self.short_strike is None or self.long_strike is None:
            raise ValueError("candidate spread requires short and long strikes")


@dataclass(slots=True)
class DecisionRecord:
    cycle_id: str
    provider: str
    action: DecisionAction
    ticker: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        try:
            self.action = DecisionAction(self.action)
        except ValueError as exc:
            raise ValueError(f"invalid decision action: {self.action}") from exc

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["action"] = self.action.value
        row["created_at"] = self.created_at.isoformat()
        return row


@dataclass(slots=True)
class AccountSnapshot:
    account_id: str = ""
    buying_power: float = 0.0
    net_liquidation: float = 0.0
    cash: float = 0.0
    excess_liquidity: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    open_risk: float = 0.0
    new_positions_today: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __init__(
        self,
        account_id: str = "",
        buying_power: float = 0.0,
        net_liquidation: float = 0.0,
        cash: float = 0.0,
        excess_liquidity: float = 0.0,
        realized_pnl: float = 0.0,
        unrealized_pnl: float = 0.0,
        open_risk: float = 0.0,
        new_positions_today: int = 0,
        updated_at: datetime | None = None,
        *,
        net_liq: float | None = None,
    ) -> None:
        self.account_id = account_id
        self.buying_power = buying_power
        self.net_liquidation = (
            net_liquidation if net_liq is None else net_liq
        )
        self.cash = cash
        self.excess_liquidity = excess_liquidity
        self.realized_pnl = realized_pnl
        self.unrealized_pnl = unrealized_pnl
        self.open_risk = open_risk
        self.new_positions_today = new_positions_today
        self.updated_at = updated_at or datetime.now(UTC)

    @property
    def net_liq(self) -> float:
        return self.net_liquidation


@dataclass(slots=True)
class PositionSnapshot:
    ticker: str
    quantity: int
    average_cost: float
    market_value: float
    unrealized_pnl: float
    side: PositionSide
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
