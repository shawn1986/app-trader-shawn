from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from trader_shawn.domain.enums import DecisionAction, OptionRight, PositionSide


def _json_safe_payload(value: Any, *, path: str = "payload") -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            item.name: _json_safe_payload(
                getattr(value, item.name),
                path=f"{path}.{item.name}",
            )
            for item in fields(value)
        }
    if isinstance(value, dict):
        serialized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"unsupported decision payload key at {path}: {key!r}"
                )
            serialized[key] = _json_safe_payload(item, path=f"{path}.{key}")
        return serialized
    if isinstance(value, list | tuple):
        return [
            _json_safe_payload(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(
        f"unsupported decision payload value at {path}: "
        f"{type(value).__name__}"
    )


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
        return {
            "cycle_id": self.cycle_id,
            "provider": self.provider,
            "action": self.action.value,
            "ticker": self.ticker,
            "payload": _json_safe_payload(self.payload),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class ManagedPositionRecord:
    position_id: str
    ticker: str
    strategy: str
    expiry: str
    short_strike: float
    long_strike: float
    quantity: int
    entry_credit: float
    entry_order_id: int | None = None
    mode: str = "paper"
    status: str = "open"
    opened_at: datetime | str = field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | str | None = None
    last_known_debit: float | None = None
    last_evaluated_at: datetime | str | None = None
    broker_fingerprint: str = ""
    decision_reason: str | None = None
    risk_note: str | None = None

    def to_row(self) -> dict[str, Any]:
        row = {
            item.name: _json_safe_payload(getattr(self, item.name), path=item.name)
            for item in fields(self)
        }
        return row


@dataclass(slots=True)
class BrokerOptionPosition:
    ticker: str
    expiry: str
    right: str
    quantity: int = 1
    short_strike: float | None = None
    long_strike: float | None = None
    average_cost: float | None = None
    market_price: float | None = None
    broker_position_id: str | None = None

    def __post_init__(self) -> None:
        self.right = self.right.upper()
        if self.right not in {"C", "P"}:
            try:
                option_right = OptionRight(self.right.lower())
            except ValueError as exc:
                raise ValueError(f"invalid option right: {self.right}") from exc
            self.right = "C" if option_right is OptionRight.CALL else "P"


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
    quantity: int = 1
    average_cost: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    side: PositionSide | str | None = None
    strategy: str = ""
    expiry: str = ""
    short_strike: float | None = None
    long_strike: float | None = None
    entry_credit: float | None = None
    current_debit: float | None = None
    dte: int | None = None
    short_leg_distance_pct: float | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.side is None:
            return
        try:
            self.side = PositionSide(self.side)
        except ValueError as exc:
            raise ValueError(f"invalid position side: {self.side}") from exc
