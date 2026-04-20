from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from trader_shawn.domain.enums import DecisionAction, OptionRight, PositionSide


@dataclass(slots=True)
class OptionQuote:
    ticker: str
    contract_symbol: str
    expiration: str
    strike: float
    right: OptionRight
    bid: float
    ask: float
    last: float | None = None
    mark: float | None = None
    volume: int = 0
    open_interest: int = 0


@dataclass(slots=True)
class CandidateSpread:
    ticker: str
    strategy: str
    short_leg: OptionQuote
    long_leg: OptionQuote
    net_credit: float
    max_loss: float
    width: float
    expiration: str


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
