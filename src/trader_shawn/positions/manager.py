from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from trader_shawn.domain.enums import PositionSide
from trader_shawn.domain.models import BrokerOptionPosition, PositionSnapshot
from trader_shawn.execution.ibkr_executor import OrderNotSubmittedError
from trader_shawn.events.earnings_calendar import EarningsCalendar

_OPENING_STALE_AFTER = timedelta(minutes=15)


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

        matched_positions = list(reconciliation["matched_positions"])
        pending_openings = list(reconciliation["pending_openings"])
        opening_to_open = list(reconciliation["opening_to_open"])
        closing_to_close = list(reconciliation["closing_to_close"])
        stale_openings = _stale_opening_positions(
            pending_openings,
            now=datetime.now(UTC),
        )
        if stale_openings:
            self._finalize_missing_closing_positions(closing_to_close)
            fingerprints: set[str] = set()
            for managed_position in stale_openings:
                recorded_at = datetime.now(UTC)
                fingerprints.add(str(managed_position["broker_fingerprint"]))
                claimed = self._audit_logger.update_managed_position_if_status(
                    managed_position["position_id"],
                    expected_status="opening",
                    status="orphaned",
                    last_evaluated_at=recorded_at.isoformat(),
                )
                if not claimed:
                    continue
                self._audit_logger.record_position_event(
                    managed_position["position_id"],
                    "open_submit_stale",
                    {
                        "broker_fingerprint": managed_position["broker_fingerprint"],
                    },
                    created_at=recorded_at,
                )
            return {
                "status": "anomaly",
                "reason": "stale_open_submission",
                "fingerprints": sorted(fingerprints),
                "manual_intervention_required": True,
            }
        for managed_position in opening_to_open:
            recorded_at = datetime.now(UTC)
            claimed = self._audit_logger.update_managed_position_if_status(
                managed_position["position_id"],
                expected_status="opening",
                status="open",
                last_evaluated_at=recorded_at.isoformat(),
            )
            if claimed:
                self._audit_logger.record_position_event(
                    managed_position["position_id"],
                    "opened",
                    {
                        "broker_fingerprint": managed_position["broker_fingerprint"],
                    },
                    created_at=recorded_at,
                )
            else:
                continue
            promoted_position = dict(managed_position)
            promoted_position["status"] = "open"
            matched_positions.append(promoted_position)
        staged_evaluations: list[dict[str, Any]] = []
        for managed_position in matched_positions:
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

        uncertain_fingerprints = self._uncertain_submit_fingerprints(matched_positions)
        if uncertain_fingerprints:
            self._finalize_missing_closing_positions(closing_to_close)
            return {
                "status": "anomaly",
                "reason": "uncertain_submit_state",
                "fingerprints": uncertain_fingerprints,
                "manual_intervention_required": True,
            }

        submission_target = next(
            (
                staged
                for staged in staged_evaluations
                if staged["managed_position"]["status"] == "open"
                and staged["exit_reason"] is not None
            ),
            None,
        )

        for staged in staged_evaluations:
            if staged is submission_target:
                continue
            managed_position = staged["managed_position"]
            snapshot = staged["snapshot"]
            updates: dict[str, Any] = {
                "last_known_debit": float(snapshot.current_debit),
                "last_evaluated_at": staged["recorded_at"].isoformat(),
            }
            self._audit_logger.update_managed_position(
                managed_position["position_id"],
                **updates,
            )

        self._finalize_missing_closing_positions(closing_to_close)

        if submission_target is not None:
            managed_position = submission_target["managed_position"]
            snapshot = submission_target["snapshot"]
            recorded_at = submission_target["recorded_at"]
            claimed = self._audit_logger.update_managed_position_if_status(
                managed_position["position_id"],
                expected_status="open",
                status="closing",
                last_known_debit=float(snapshot.current_debit),
                last_evaluated_at=recorded_at.isoformat(),
            )
            if not claimed:
                return {
                    "status": "ok",
                    "managed_count": len(managed_positions),
                }

            try:
                submission = self._executor.submit_limit_combo(
                    snapshot,
                    limit_price=float(snapshot.current_debit),
                )
            except OrderNotSubmittedError:
                self._audit_logger.update_managed_position_if_status(
                    managed_position["position_id"],
                    expected_status="closing",
                    status="open",
                )
                raise
            except Exception as exc:
                self._audit_logger.record_position_event(
                    managed_position["position_id"],
                    "close_submit_uncertain",
                    {
                        "exit_reason": submission_target["exit_reason"],
                        "limit_price": float(snapshot.current_debit),
                        "broker_fingerprint": managed_position["broker_fingerprint"],
                        "error": str(exc),
                    },
                    created_at=recorded_at,
                )
                raise

            exit_reason = submission_target["exit_reason"]
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

    def _finalize_missing_closing_positions(
        self,
        managed_positions: list[dict[str, Any]],
    ) -> None:
        for managed_position in managed_positions:
            recorded_at = datetime.now(UTC)
            claimed = self._audit_logger.update_managed_position_if_status(
                managed_position["position_id"],
                expected_status="closing",
                status="closed",
                closed_at=recorded_at.isoformat(),
                last_evaluated_at=recorded_at.isoformat(),
            )
            if not claimed:
                continue
            self._audit_logger.record_position_event(
                managed_position["position_id"],
                "closed",
                {
                    "reason": "broker_position_missing",
                    "broker_fingerprint": managed_position["broker_fingerprint"],
                },
                created_at=recorded_at,
            )

    def _uncertain_submit_fingerprints(
        self,
        managed_positions: list[dict[str, Any]],
    ) -> list[str]:
        fingerprints: set[str] = set()
        for managed_position in managed_positions:
            if managed_position["status"] != "closing":
                continue
            events = self._audit_logger.fetch_position_events(
                managed_position["position_id"]
            )
            if events and events[-1]["event_type"] == "close_submit_uncertain":
                fingerprints.add(str(managed_position["broker_fingerprint"]))
        return sorted(fingerprints)

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
    duplicate_fingerprints = _duplicate_saved_fingerprints(managed_positions)
    if duplicate_fingerprints:
        return _anomaly_result(
            reason="unknown_broker_position",
            fingerprints=duplicate_fingerprints,
        )

    available_legs = [_broker_leg_key(position) for position in broker_positions]
    matched_positions: list[dict[str, Any]] = []
    pending_openings: list[dict[str, Any]] = []
    opening_to_open: list[dict[str, Any]] = []
    closing_to_close: list[dict[str, Any]] = []
    missing_fingerprints: list[str] = []
    unknown_fingerprints: set[str] = set()

    for managed_position in managed_positions:
        identity = _managed_identity(managed_position)
        stored_identity = _stored_identity(managed_position)
        if stored_identity != identity:
            unknown_fingerprints.add(identity[0])
            stored_leg_indexes = _find_stored_leg_indexes(
                available_legs,
                managed_position=managed_position,
            )
            if stored_leg_indexes is not None:
                _remove_matched_legs(available_legs, stored_leg_indexes)
            continue

        leg_indexes = _find_matching_leg_indexes(
            available_legs,
            managed_position=managed_position,
        )
        if leg_indexes is None:
            if managed_position["status"] == "closing":
                closing_to_close.append(managed_position)
                continue
            if managed_position["status"] == "opening":
                pending_openings.append(managed_position)
                continue
            missing_fingerprints.append(identity[0])
            continue

        _remove_matched_legs(available_legs, leg_indexes)
        if managed_position["status"] == "opening":
            opening_to_open.append(managed_position)
            continue
        matched_positions.append(managed_position)

    leftover_fingerprints = _leftover_broker_fingerprints(available_legs)
    if leftover_fingerprints is None:
        return _anomaly_result(
            reason="unknown_broker_position",
            fingerprints=sorted(unknown_fingerprints),
        )
    unknown_fingerprints.update(leftover_fingerprints)

    if unknown_fingerprints:
        return _anomaly_result(
            reason="unknown_broker_position",
            fingerprints=sorted(unknown_fingerprints),
        )

    if missing_fingerprints:
        return _anomaly_result(
            reason="missing_broker_position",
            fingerprints=sorted(missing_fingerprints),
        )

    return {
        "status": "ok",
        "matched_positions": matched_positions,
        "pending_openings": pending_openings,
        "opening_to_open": opening_to_open,
        "closing_to_close": closing_to_close,
    }


def _anomaly_result(*, reason: str, fingerprints: list[str]) -> dict[str, object]:
    return {
        "status": "anomaly",
        "reason": reason,
        "fingerprints": fingerprints,
    }


def _stale_opening_positions(
    managed_positions: list[dict[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    stale: list[dict[str, Any]] = []
    for managed_position in managed_positions:
        opened_at = _parse_managed_datetime(managed_position.get("opened_at"))
        if opened_at is None:
            stale.append(managed_position)
            continue
        if now - opened_at >= _OPENING_STALE_AFTER:
            stale.append(managed_position)
    return stale


def _parse_managed_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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


def _duplicate_saved_fingerprints(
    managed_positions: list[dict[str, Any]],
) -> list[str]:
    identity_counts: dict[tuple[str, str], int] = defaultdict(int)
    for managed_position in managed_positions:
        identity_counts[_managed_identity(managed_position)] += 1
    return _identity_fingerprints(
        {
            identity
            for identity, count in identity_counts.items()
            if count > 1
        }
    )


def _managed_fingerprint(managed_position: dict[str, Any]) -> str:
    strategy = str(managed_position["strategy"])
    return (
        f"{managed_position['ticker']}|{managed_position['expiry']}|"
        f"{_option_right_for_strategy(strategy)}|"
        f"{float(managed_position['short_strike'])}|"
        f"{float(managed_position['long_strike'])}|"
        f"{int(managed_position['quantity'])}"
    )


def _broker_leg_key(
    broker_position: BrokerOptionPosition,
) -> tuple[str, str, str, int, float]:
    if broker_position.short_strike is None:
        raise ValueError("broker option position is missing strike")
    return (
        broker_position.ticker,
        broker_position.expiry,
        broker_position.right,
        int(broker_position.quantity),
        float(broker_position.short_strike),
    )


def _find_matching_leg_indexes(
    available_legs: list[tuple[str, str, str, int, float]],
    *,
    managed_position: dict[str, Any],
) -> tuple[int, int] | None:
    short_leg, long_leg = _expected_broker_leg_keys(managed_position)
    short_index = _find_leg_index(available_legs, short_leg)
    if short_index is None:
        return None
    long_index = _find_leg_index(
        available_legs,
        long_leg,
        skip_index=short_index,
    )
    if long_index is None:
        return None
    return short_index, long_index


def _find_stored_leg_indexes(
    available_legs: list[tuple[str, str, str, int, float]],
    *,
    managed_position: dict[str, Any],
) -> tuple[int, int] | None:
    stored_leg_keys = _stored_broker_leg_keys(managed_position)
    if stored_leg_keys is None:
        return None
    short_leg, long_leg = stored_leg_keys
    short_index = _find_leg_index(available_legs, short_leg)
    if short_index is None:
        return None
    long_index = _find_leg_index(
        available_legs,
        long_leg,
        skip_index=short_index,
    )
    if long_index is None:
        return None
    return short_index, long_index


def _expected_broker_leg_keys(
    managed_position: dict[str, Any],
) -> tuple[
    tuple[str, str, str, int, float],
    tuple[str, str, str, int, float],
]:
    right = _option_right_for_strategy(str(managed_position["strategy"]))
    quantity = int(managed_position["quantity"])
    ticker = str(managed_position["ticker"])
    expiry = str(managed_position["expiry"])
    return (
        (
            ticker,
            expiry,
            right,
            -quantity,
            float(managed_position["short_strike"]),
        ),
        (
            ticker,
            expiry,
            right,
            quantity,
            float(managed_position["long_strike"]),
        ),
    )


def _stored_broker_leg_keys(
    managed_position: dict[str, Any],
) -> tuple[
    tuple[str, str, str, int, float],
    tuple[str, str, str, int, float],
] | None:
    parts = str(managed_position["broker_fingerprint"]).split("|")
    if len(parts) != 6:
        return None
    ticker, expiry, right, short_strike, long_strike, quantity = parts
    try:
        parsed_quantity = int(quantity)
        parsed_short_strike = float(short_strike)
        parsed_long_strike = float(long_strike)
    except ValueError:
        return None
    return (
        (
            ticker,
            expiry,
            right,
            -parsed_quantity,
            parsed_short_strike,
        ),
        (
            ticker,
            expiry,
            right,
            parsed_quantity,
            parsed_long_strike,
        ),
    )


def _find_leg_index(
    available_legs: list[tuple[str, str, str, int, float]],
    expected_leg: tuple[str, str, str, int, float],
    *,
    skip_index: int | None = None,
) -> int | None:
    for index, leg in enumerate(available_legs):
        if skip_index is not None and index == skip_index:
            continue
        if leg == expected_leg:
            return index
    return None


def _remove_matched_legs(
    available_legs: list[tuple[str, str, str, int, float]],
    indexes: tuple[int, int],
) -> None:
    for index in sorted(indexes, reverse=True):
        available_legs.pop(index)


def _leftover_broker_fingerprints(
    available_legs: list[tuple[str, str, str, int, float]],
) -> set[str] | None:
    grouped_legs: dict[
        tuple[str, str, str, int],
        dict[str, list[float]],
    ] = defaultdict(lambda: {"short": [], "long": []})
    for ticker, expiry, right, quantity, strike in available_legs:
        if quantity == 0:
            return None
        key = (ticker, expiry, right, abs(quantity))
        bucket = "short" if quantity < 0 else "long"
        grouped_legs[key][bucket].append(strike)

    fingerprints: set[str] = set()
    for (ticker, expiry, right, quantity), grouped in grouped_legs.items():
        short_strikes = grouped["short"]
        long_strikes = grouped["long"]
        if len(short_strikes) != len(long_strikes):
            return None
        ordered_pairs = _pair_leftover_strikes(
            right=right,
            short_strikes=short_strikes,
            long_strikes=long_strikes,
        )
        if ordered_pairs is None:
            return None
        for short_strike, long_strike in ordered_pairs:
            fingerprints.add(
                (
                    f"{ticker}|{expiry}|{right}|{float(short_strike)}|"
                    f"{float(long_strike)}|{quantity}"
                )
            )
    return fingerprints


def _pair_leftover_strikes(
    *,
    right: str,
    short_strikes: list[float],
    long_strikes: list[float],
) -> list[tuple[float, float]] | None:
    if right == "P":
        ordered_shorts = sorted(short_strikes, reverse=True)
        ordered_longs = sorted(long_strikes, reverse=True)
        if any(short <= long for short, long in zip(ordered_shorts, ordered_longs)):
            return None
        return list(zip(ordered_shorts, ordered_longs, strict=True))
    if right == "C":
        ordered_shorts = sorted(short_strikes)
        ordered_longs = sorted(long_strikes)
        if any(short >= long for short, long in zip(ordered_shorts, ordered_longs)):
            return None
        return list(zip(ordered_shorts, ordered_longs, strict=True))
    return None


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
