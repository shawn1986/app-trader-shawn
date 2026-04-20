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

    @property
    def expiration(self) -> str:
        return self.expiry

    @property
    def contract_symbol(self) -> str:
        return f"{self.symbol}-{self.expiry}-{self.right}{self.strike:g}"


@dataclass(slots=True)
class CandidateSpread:
    symbol: str
    strategy: str
    short_leg: OptionQuote
    long_leg: OptionQuote
    credit: float
    max_loss: float
    width: float
    expiry: str

    @property
    def ticker(self) -> str:
        return self.symbol

    @property
    def net_credit(self) -> float:
        return self.credit

    @property
    def expiration(self) -> str:
        return self.expiry

    @property
    def short_strike(self) -> float:
        return self.short_leg.strike

    @property
    def long_strike(self) -> float:
        return self.long_leg.strike


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
    account_id: str
    buying_power: float
    net_liquidation: float
    cash: float
    excess_liquidity: float
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class PositionSnapshot:
    ticker: str
    quantity: int
    average_cost: float
    market_value: float
    unrealized_pnl: float
    side: PositionSide
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
