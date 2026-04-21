from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ThreatLevel = Literal["normal", "warning", "critical"]
BrokerState = Literal["healthy", "degraded"]
Freshness = Literal["fresh", "stale", "unknown"]


@dataclass(slots=True)
class BrokerCommandStatus:
    state: BrokerState
    freshness: Freshness
    checked_at: str | None
    latency_ms: int | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "freshness": self.freshness,
            "checked_at": self.checked_at,
            "latency_ms": self.latency_ms,
            "message": self.message,
        }


@dataclass(slots=True)
class CommandStatus:
    broker: BrokerCommandStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "broker": self.broker.to_dict(),
        }


@dataclass(slots=True)
class ThreatRail:
    cycle_status: str
    reason: str
    manual_intervention_required: bool
    fingerprints: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_status": self.cycle_status,
            "reason": self.reason,
            "manual_intervention_required": self.manual_intervention_required,
            "fingerprints": list(self.fingerprints),
        }


@dataclass(slots=True)
class HotPosition:
    position_id: str
    ticker: str
    status: str
    expiry: str
    last_known_debit: float | None
    latest_event_type: str | None
    latest_event_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "ticker": self.ticker,
            "status": self.status,
            "expiry": self.expiry,
            "last_known_debit": self.last_known_debit,
            "latest_event_type": self.latest_event_type,
            "latest_event_at": self.latest_event_at,
        }


@dataclass(slots=True)
class AccountRail:
    net_liquidation: float
    unrealized_pnl: float
    open_risk: float
    new_positions_today: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "net_liquidation": self.net_liquidation,
            "unrealized_pnl": self.unrealized_pnl,
            "open_risk": self.open_risk,
            "new_positions_today": self.new_positions_today,
        }


@dataclass(slots=True)
class WarRoomSnapshot:
    threat_level: ThreatLevel
    command_status: CommandStatus
    threat_rail: ThreatRail
    account_rail: AccountRail
    hot_positions: list[HotPosition]

    def to_dict(self) -> dict[str, Any]:
        return {
            "threat_level": self.threat_level,
            "command_status": self.command_status.to_dict(),
            "threat_rail": self.threat_rail.to_dict(),
            "account_rail": self.account_rail.to_dict(),
            "hot_positions": [position.to_dict() for position in self.hot_positions],
        }
