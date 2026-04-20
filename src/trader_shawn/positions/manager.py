from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from trader_shawn.domain.enums import PositionSide
from trader_shawn.domain.models import BrokerOptionPosition, PositionSnapshot
from trader_shawn.events.earnings_calendar import EarningsCalendar


class PositionManager:
    def __init__(
        self,
        *,
        audit_logger: Any,
        market_data: Any,
        executor: Any,
        earnings_calendar: EarningsCalendar | None,
        risk_settings: Any,
        mode: str,
        as_of: date | None = None,
    ) -> None:
        self._audit_logger = audit_logger
        self._market_data = market_data
        self._executor = executor
        self._earnings_calendar = earnings_calendar
        self._risk_settings = risk_settings
        self._mode = mode
        self._as_of = as_of

    def manage_positions(self) -> dict[str, object]:
        managed_positions = self._audit_logger.fetch_active_managed_positions(
            mode=self._mode
        )
        broker_positions = self._market_data.fetch_option_positions()
        reconciliation = _reconcile_positions(
            managed_positions=managed_positions,
            broker_positions=broker_positions,
        )
        if reconciliation["status"] == "anomaly":
            return reconciliation

        staged_evaluations: list[dict[str, Any]] = []
        for managed_position in managed_positions:
            snapshot = self._build_snapshot(managed_position)
            exit_reason = evaluate_exit(
                snapshot,
                profit_take_pct=float(self._risk_settings.profit_take_pct),
                stop_loss_multiple=float(self._risk_settings.stop_loss_multiple),
                exit_dte_threshold=int(self._risk_settings.exit_dte_threshold),
                earnings_calendar=self._earnings_calendar,
                as_of=self._effective_as_of(),
            )
            staged_evaluations.append(
                {
                    "managed_position": managed_position,
                    "snapshot": snapshot,
                    "exit_reason": exit_reason,
                    "recorded_at": datetime.now(UTC),
                }
            )

        submission_target = next(
            (
                staged
                for staged in staged_evaluations
                if staged["managed_position"]["status"] == "open"
                and staged["exit_reason"] is not None
            ),
            None,
        )
        submission: dict[str, object] | None = None
        if submission_target is not None:
            snapshot = submission_target["snapshot"]
            submission = self._executor.submit_limit_combo(
                snapshot,
                limit_price=float(snapshot.current_debit),
            )

        for staged in staged_evaluations:
            managed_position = staged["managed_position"]
            snapshot = staged["snapshot"]
            updates: dict[str, Any] = {
                "last_known_debit": float(snapshot.current_debit),
                "last_evaluated_at": staged["recorded_at"].isoformat(),
            }
            if staged is submission_target:
                updates["status"] = "closing"
            self._audit_logger.update_managed_position(
                managed_position["position_id"],
                **updates,
            )

        if submission_target is not None and submission is not None:
            managed_position = submission_target["managed_position"]
            snapshot = submission_target["snapshot"]
            exit_reason = submission_target["exit_reason"]
            recorded_at = submission_target["recorded_at"]
            self._audit_logger.record_position_event(
                managed_position["position_id"],
                "close_submitted",
                {
                    "exit_reason": exit_reason,
                    "limit_price": float(snapshot.current_debit),
                    "order_id": submission.get("order_id"),
                    "broker_fingerprint": submission.get(
                        "broker_fingerprint",
                        managed_position["broker_fingerprint"],
                    ),
                },
                created_at=recorded_at,
            )
            return {
                "status": "submitted",
                "position_id": managed_position["position_id"],
                "ticker": snapshot.ticker,
                "exit_reason": exit_reason,
                "payload": submission,
            }

        return {
            "status": "ok",
            "managed_count": len(managed_positions),
        }

    def _build_snapshot(
        self,
        managed_position: dict[str, Any],
    ) -> PositionSnapshot:
        current_debit = self._market_data.estimate_spread_debit(
            ticker=str(managed_position["ticker"]),
            expiry=str(managed_position["expiry"]),
            short_strike=float(managed_position["short_strike"]),
            long_strike=float(managed_position["long_strike"]),
            strategy=str(managed_position["strategy"]),
        )
        spot_price = self._market_data.fetch_spot_price(str(managed_position["ticker"]))
        return PositionSnapshot(
            ticker=str(managed_position["ticker"]),
            quantity=int(managed_position["quantity"]),
            side=PositionSide.SHORT,
            strategy=str(managed_position["strategy"]),
            expiry=str(managed_position["expiry"]),
            short_strike=float(managed_position["short_strike"]),
            long_strike=float(managed_position["long_strike"]),
            entry_credit=float(managed_position["entry_credit"]),
            current_debit=float(current_debit),
            dte=_days_to_expiry(
                expiry=str(managed_position["expiry"]),
                as_of=self._effective_as_of(),
            ),
            short_leg_distance_pct=_short_leg_distance_pct(
                strategy=str(managed_position["strategy"]),
                short_strike=float(managed_position["short_strike"]),
                spot_price=float(spot_price),
            ),
        )

    def _effective_as_of(self) -> date:
        return self._as_of or date.today()


def _require_exit_field(position: PositionSnapshot, field_name: str) -> float | int:
    value = getattr(position, field_name)
    if value is None:
        raise ValueError(f"missing required exit field: {field_name}")
    return value


def evaluate_exit(
    position: PositionSnapshot,
    *,
    profit_take_pct: float,
    stop_loss_multiple: float,
    exit_dte_threshold: int,
    short_strike_distance_threshold_pct: float = 0.02,
    earnings_calendar: EarningsCalendar | None = None,
    as_of: date | None = None,
) -> str | None:
    entry_credit = _require_exit_field(position, "entry_credit")
    current_debit = _require_exit_field(position, "current_debit")
    dte = _require_exit_field(position, "dte")

    if entry_credit <= 0:
        return None

    if current_debit <= entry_credit * (1 - profit_take_pct):
        return "take_profit"

    if current_debit >= entry_credit * stop_loss_multiple:
        return "stop_loss"

    if _has_blocking_event(position, earnings_calendar=earnings_calendar, as_of=as_of):
        return "event_risk_exit"

    if dte <= exit_dte_threshold:
        return "dte_exit"

    if (
        position.short_leg_distance_pct is not None
        and position.short_leg_distance_pct <= short_strike_distance_threshold_pct
    ):
        return "short_strike_proximity"

    return None


def _has_blocking_event(
    position: PositionSnapshot,
    *,
    earnings_calendar: EarningsCalendar | None,
    as_of: date | None,
) -> bool:
    if earnings_calendar is None or not position.expiry:
        return False
    start = as_of or date.today()
    return earnings_calendar.has_blocking_event(
        position.ticker,
        start,
        date.fromisoformat(position.expiry),
    )


def _reconcile_positions(
    *,
    managed_positions: list[dict[str, Any]],
    broker_positions: list[BrokerOptionPosition],
) -> dict[str, object]:
    if not managed_positions and broker_positions:
        broker_identities = _broker_identities(broker_positions)
        return _anomaly_result(
            reason="unknown_broker_position",
            fingerprints=(
                []
                if broker_identities is None
                else _identity_fingerprints(broker_identities)
            ),
        )

    broker_identities = _broker_identities(broker_positions)
    if broker_identities is None:
        return _anomaly_result(
            reason="unknown_broker_position",
            fingerprints=[],
        )

    remaining_identities = set(broker_identities)
    missing_fingerprints: list[str] = []
    for managed_position in managed_positions:
        identity = _managed_identity(managed_position)
        stored_identity = _stored_identity(managed_position)
        if stored_identity != identity:
            missing_fingerprints.append(identity[0])
            if identity in remaining_identities:
                remaining_identities.remove(identity)
            continue
        if identity not in remaining_identities:
            missing_fingerprints.append(identity[0])
            continue
        remaining_identities.remove(identity)

    if missing_fingerprints:
        return _anomaly_result(
            reason="missing_broker_position",
            fingerprints=sorted(missing_fingerprints),
        )

    if remaining_identities:
        return _anomaly_result(
            reason="unknown_broker_position",
            fingerprints=_identity_fingerprints(remaining_identities),
        )

    return {"status": "ok"}


def _anomaly_result(*, reason: str, fingerprints: list[str]) -> dict[str, object]:
    return {
        "status": "anomaly",
        "reason": reason,
        "fingerprints": fingerprints,
    }


def _managed_identity(
    managed_position: dict[str, Any],
) -> tuple[str, str]:
    return (
        _managed_fingerprint(managed_position),
        str(managed_position["strategy"]),
    )


def _stored_identity(
    managed_position: dict[str, Any],
) -> tuple[str, str]:
    return (
        str(managed_position["broker_fingerprint"]),
        str(managed_position["strategy"]),
    )


def _identity_fingerprints(identities: set[tuple[str, str]]) -> list[str]:
    return sorted({fingerprint for fingerprint, _ in identities})


def _managed_fingerprint(managed_position: dict[str, Any]) -> str:
    strategy = str(managed_position["strategy"])
    return (
        f"{managed_position['ticker']}|{managed_position['expiry']}|"
        f"{_option_right_for_strategy(strategy)}|"
        f"{float(managed_position['short_strike'])}|"
        f"{float(managed_position['long_strike'])}|"
        f"{int(managed_position['quantity'])}"
    )


def _broker_identities(
    broker_positions: list[BrokerOptionPosition],
) -> set[tuple[str, str]] | None:
    grouped_positions: dict[
        tuple[str, str, str, int],
        dict[str, list[BrokerOptionPosition]],
    ] = defaultdict(lambda: {"short": [], "long": []})
    for broker_position in broker_positions:
        if broker_position.short_strike is None:
            return None
        quantity = int(broker_position.quantity)
        if quantity == 0:
            return None
        key = (
            broker_position.ticker,
            broker_position.expiry,
            broker_position.right,
            abs(quantity),
        )
        bucket = "short" if quantity < 0 else "long"
        grouped_positions[key][bucket].append(broker_position)

    identities: set[tuple[str, str]] = set()
    for (ticker, expiry, right, quantity), grouped in grouped_positions.items():
        short_legs = grouped["short"]
        long_legs = grouped["long"]
        if len(short_legs) != 1 or len(long_legs) != 1:
            return None
        short_leg = short_legs[0]
        long_leg = long_legs[0]
        strategy = _infer_credit_spread_strategy(
            right=right,
            short_strike=float(short_leg.short_strike),
            long_strike=float(long_leg.short_strike),
        )
        if strategy is None:
            return None
        identities.add(
            (
                (
                    f"{ticker}|{expiry}|{right}|{float(short_leg.short_strike)}|"
                    f"{float(long_leg.short_strike)}|{quantity}"
                ),
                strategy,
            )
        )
    return identities


def _option_right_for_strategy(strategy: str) -> str:
    normalized_strategy = strategy.lower()
    if normalized_strategy == "bull_put_credit_spread":
        return "P"
    if normalized_strategy == "bear_call_credit_spread":
        return "C"
    raise ValueError(f"unsupported credit spread strategy: {strategy}")


def _infer_credit_spread_strategy(
    *,
    right: str,
    short_strike: float,
    long_strike: float,
) -> str | None:
    if right == "P":
        if short_strike > long_strike:
            return "bull_put_credit_spread"
        return None
    if right == "C":
        if short_strike < long_strike:
            return "bear_call_credit_spread"
        return None
    return None


def _days_to_expiry(*, expiry: str, as_of: date) -> int:
    return (date.fromisoformat(expiry) - as_of).days


def _short_leg_distance_pct(
    *,
    strategy: str,
    short_strike: float,
    spot_price: float,
) -> float | None:
    if spot_price <= 0:
        return None
    normalized_strategy = strategy.lower()
    if normalized_strategy == "bull_put_credit_spread":
        return (spot_price - short_strike) / spot_price
    if normalized_strategy == "bear_call_credit_spread":
        return (short_strike - spot_price) / spot_price
    raise ValueError(f"unsupported credit spread strategy: {strategy}")
