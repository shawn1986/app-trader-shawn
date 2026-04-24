from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
import inspect
import json
import math
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

import click
import uvicorn

from trader_shawn.ai.claude_cli_adapter import ClaudeCliAdapter
from trader_shawn.ai.codex_adapter import CodexAdapter
from trader_shawn.ai.service import AiDecisionService
from trader_shawn.candidate_builder.credit_spread_builder import build_candidates
from trader_shawn.candidate_builder.paper_watchlist_builder import build_paper_watchlist
from trader_shawn.domain.enums import DecisionAction
from trader_shawn.domain.models import AccountSnapshot, CandidateSpread, ManagedPositionRecord
from trader_shawn.events.earnings_calendar import EarningsCalendar
from trader_shawn.execution.ibkr_executor import IbkrExecutor, OrderNotSubmittedError
from trader_shawn.market_data.ibkr_market_data import IbkrMarketDataClient
from trader_shawn.monitoring.audit_logger import AuditLogger
from trader_shawn.monitoring.dashboard_api import read_dashboard_snapshot, update_dashboard_state
from trader_shawn.positions.manager import PositionManager
from trader_shawn.risk.guard import RiskGuard
from trader_shawn.settings import AppSettings, load_settings


@dataclass(slots=True)
class CliRuntime:
    settings: AppSettings
    config_dir: Path
    scanner: Any | None = None
    account_service: Any | None = None
    decision_service: Any | None = None
    executor: Any | None = None
    risk_guard: Any | None = None
    position_service: Any | None = None
    position_manager: Any | None = None
    audit_logger: Any | None = None
    dashboard_state_path: Path | None = None
    progress_callback: Callable[[dict[str, Any]], None] | None = None


@dataclass(slots=True)
class ScanMarketResult:
    candidates: list[CandidateSpread]
    watchlist: list[Any] = field(default_factory=list)
    symbol_errors: list[dict[str, str]] = field(default_factory=list)
    symbol_summaries: list[dict[str, Any]] = field(default_factory=list)


class CliScanner:
    def __init__(
        self,
        *,
        market_data_client: IbkrMarketDataClient,
        earnings_calendar: EarningsCalendar,
        mode: str,
        candidate_filters: Any | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._market_data_client = market_data_client
        self._earnings_calendar = earnings_calendar
        self._mode = mode
        self._candidate_filters = candidate_filters
        self._progress_callback = progress_callback

    def set_progress_callback(
        self,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self._progress_callback = progress_callback

    def _emit_progress(self, stage: str, message: str, **details: Any) -> None:
        if not callable(self._progress_callback):
            return
        self._progress_callback(
            {
                "stage": stage,
                "message": message,
                **details,
            }
        )

    def scan_candidates(self, symbols: list[str]) -> list[CandidateSpread]:
        return self.scan_market(symbols).candidates

    def scan_market(self, symbols: list[str]) -> ScanMarketResult:
        as_of = date.today()
        candidates: list[CandidateSpread] = []
        watchlist: list[Any] = []
        symbol_errors: list[dict[str, str]] = []
        symbol_summaries: list[dict[str, Any]] = []
        total_symbols = len(symbols)
        self._emit_progress(
            "scan_started",
            "Scan initiated.",
            current=0,
            total=total_symbols,
            unit="symbols",
        )
        for index, symbol in enumerate(symbols, start=1):
            symbol_candidate_count_before = len(candidates)
            symbol_watchlist_count_before = len(watchlist)
            market_data_type = getattr(
                self._market_data_client,
                "market_data_type",
                getattr(self._market_data_client, "_market_data_type", "unknown"),
            )
            self._emit_progress(
                "scan_symbol_fetching",
                f"Fetching {symbol} option quotes.",
                symbol=symbol,
                current=index - 1,
                total=total_symbols,
                unit="symbols",
            )
            click.echo(
                f"scan: fetching {symbol} option quotes ({market_data_type} market data)",
                err=True,
            )
            try:
                quotes = self._fetch_symbol_option_quotes(
                    symbol,
                    current=index - 1,
                    total=total_symbols,
                )
            except Exception as exc:
                error_message = _exception_message(exc)
                symbol_errors.append(
                    {
                        "symbol": symbol,
                        "error_type": type(exc).__name__,
                        "message": error_message,
                    }
                )
                symbol_summaries.append(
                    {
                        "symbol": symbol,
                        "quotes_count": 0,
                        "candidate_count": 0,
                        "watchlist_count": 0,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "message": error_message,
                    }
                )
                self._emit_progress(
                    "scan_symbol_failed",
                    f"{symbol} failed: {error_message}",
                    symbol=symbol,
                    error_type=type(exc).__name__,
                    current=index,
                    total=total_symbols,
                    unit="symbols",
                )
                click.echo(f"scan: {symbol} failed: {error_message}", err=True)
                continue
            click.echo(f"scan: {symbol} returned {len(quotes)} option quotes", err=True)
            quotes_by_expiry: dict[str, list[Any]] = {}
            for quote in quotes:
                quotes_by_expiry.setdefault(quote.expiry, []).append(quote)
            for expiry, expiry_quotes in sorted(quotes_by_expiry.items()):
                dte = (date.fromisoformat(expiry) - as_of).days
                if dte < 0:
                    continue
                expiry_candidates = build_candidates(
                    symbol,
                    dte,
                    expiry_quotes,
                    earnings_calendar=self._earnings_calendar,
                    as_of=as_of,
                    filters=self._candidate_filters,
                )
                candidates.extend(expiry_candidates)
                if self._mode == "paper" and not expiry_candidates:
                    watchlist.extend(
                        build_paper_watchlist(
                            symbol,
                            dte,
                            expiry_quotes,
                            earnings_calendar=self._earnings_calendar,
                            as_of=as_of,
                            filters=self._candidate_filters,
                        )
                    )
            self._emit_progress(
                "scan_symbol_completed",
                (
                    f"{symbol} complete. {len(quotes)} quotes, "
                    f"{len(candidates) - symbol_candidate_count_before} candidates, "
                    f"{len(watchlist) - symbol_watchlist_count_before} watchlist."
                ),
                symbol=symbol,
                quotes_count=len(quotes),
                candidate_count=len(candidates) - symbol_candidate_count_before,
                watchlist_count=len(watchlist) - symbol_watchlist_count_before,
                cumulative_candidate_count=len(candidates),
                cumulative_watchlist_count=len(watchlist),
                current=index,
                total=total_symbols,
                unit="symbols",
            )
            symbol_summaries.append(
                {
                    "symbol": symbol,
                    "quotes_count": len(quotes),
                    "candidate_count": len(candidates) - symbol_candidate_count_before,
                    "watchlist_count": len(watchlist) - symbol_watchlist_count_before,
                    "status": "ok",
                }
            )
        self._emit_progress(
            "scan_completed",
            (
                f"Scan complete. {len(candidates)} candidates, "
                f"{len(watchlist)} watchlist observations."
            ),
            candidate_count=len(candidates),
            watchlist_count=len(watchlist),
            current=total_symbols,
            total=total_symbols,
            unit="symbols",
        )
        return ScanMarketResult(
            candidates=candidates,
            watchlist=watchlist,
            symbol_errors=symbol_errors,
            symbol_summaries=symbol_summaries,
        )

    def _fetch_symbol_option_quotes(
        self,
        symbol: str,
        *,
        current: int,
        total: int,
    ) -> list[Any]:
        fetch_option_quotes = self._market_data_client.fetch_option_quotes

        def progress_callback(event: dict[str, Any]) -> None:
            payload = dict(event)
            payload.setdefault("symbol", symbol)
            payload.setdefault("current", current)
            payload.setdefault("total", total)
            payload.setdefault("unit", "symbols")
            if "stage" not in payload:
                payload["stage"] = "scan_symbol_progress"
            if "message" not in payload:
                payload["message"] = f"{symbol} scan step in progress."
            self._emit_progress(
                str(payload.pop("stage")),
                str(payload.pop("message")),
                **payload,
            )

        if _callable_accepts_keyword(fetch_option_quotes, "progress_callback"):
            return list(
                fetch_option_quotes(
                    symbol,
                    progress_callback=progress_callback,
                )
            )
        return list(fetch_option_quotes(symbol))


def run_trade_cycle(
    *,
    candidates: Sequence[CandidateSpread],
    account: AccountSnapshot,
    decision_service: Any,
    executor: Any,
    risk_guard: Any | None = None,
    open_symbol_count: int = 0,
) -> dict[str, Any]:
    terminal_result, matched_spread, limit_credit = _resolve_trade_decision(
        candidates=candidates,
        account=account,
        decision_service=decision_service,
    )
    if terminal_result is not None:
        return terminal_result

    if risk_guard is None:
        return {"status": "risk_rejected", "reason": "risk_guard_missing"}

    guard_result = risk_guard.evaluate(
        matched_spread,
        account,
        open_symbol_count,
    )
    if not guard_result.allowed:
        return {"status": "risk_rejected", "reason": guard_result.reason}

    try:
        return executor.submit_open_credit_spread(
            matched_spread,
            limit_credit=limit_credit,
            quantity=1,
        )
    except Exception as exc:
        return _error_result(
            status="executor_error",
            reason="submission_failed",
            exc=exc,
        )


def _resolve_trade_decision(
    *,
    candidates: Sequence[CandidateSpread],
    account: AccountSnapshot,
    decision_service: Any,
) -> tuple[dict[str, Any] | None, CandidateSpread | None, float | None]:
    if not candidates:
        return {"status": "no_candidates"}, None, None

    selected = candidates[0]
    context = {
        "ticker": selected.ticker,
        "candidate": selected,
        "candidates": list(candidates),
        "account": account,
    }
    try:
        decision = decision_service.decide(context)
    except Exception as exc:
        return (
            _error_result(
                status="decision_error",
                reason="decision_service_failed",
                exc=exc,
            ),
            None,
            None,
        )

    try:
        action = DecisionAction(getattr(decision, "action"))
    except (AttributeError, TypeError, ValueError):
        return (
            {
                "status": "decision_error",
                "reason": "invalid_action",
                "action": getattr(decision, "action", None),
            },
            None,
            None,
        )
    if action is not DecisionAction.APPROVE:
        return (
            {
                "status": "decision_rejected",
                "reason": getattr(decision, "reason", ""),
                "action": action.value,
            },
            None,
            None,
        )

    try:
        approved_candidate_key = _approved_candidate_key(decision)
        limit_credit = _decision_limit_credit(decision)
    except (AttributeError, TypeError, ValueError) as exc:
        return (
            _error_result(
                status="decision_error",
                reason="invalid_approval",
                exc=exc,
            ),
            None,
            None,
        )

    matched_spread = _resolve_approved_candidate(candidates, approved_candidate_key)
    if matched_spread is None:
        return (
            {
                "status": "decision_rejected",
                "reason": "approved_candidate_not_found",
            },
            None,
            None,
        )

    return None, matched_spread, limit_credit


@click.group(name="trader-shawn")
def cli() -> None:
    """Trader Shawn command line interface."""


@cli.command("trade-cycle")
def trade_cycle_command() -> None:
    click.echo(json.dumps(_trade_cycle_command(), sort_keys=True))


@cli.command("scan")
def scan_command() -> None:
    click.echo(json.dumps(_scan_command(), sort_keys=True))


@cli.command("decide")
def decide_command() -> None:
    click.echo(json.dumps(_decide_command(), sort_keys=True))


@cli.command("trade")
def trade_command() -> None:
    click.echo(json.dumps(_trade_command(), sort_keys=True))


@cli.command("manage")
def manage_command() -> None:
    click.echo(json.dumps(_manage_command(), sort_keys=True))


@cli.command("dashboard")
@click.argument("state_path", type=click.Path(path_type=Path))
def dashboard_command(state_path: Path) -> None:
    click.echo(json.dumps(_dashboard_command(state_path), sort_keys=True))


@cli.command("war-room")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8787, show_default=True, type=int)
def war_room_command(host: str, port: int) -> None:
    if host.strip().lower() not in {"127.0.0.1", "localhost"}:
        raise click.ClickException(
            "war-room only supports loopback host binding (127.0.0.1 or localhost)"
        )

    from trader_shawn.war_room.web import create_war_room_app

    uvicorn.run(create_war_room_app(), host=host, port=port)


def main(argv: Sequence[str] | None = None) -> int:
    cli.main(
        args=list(argv) if argv is not None else None,
        prog_name="trader-shawn",
        standalone_mode=False,
    )
    return 0


def _approved_candidate_key(decision: Any) -> tuple[str, str, float, float]:
    return (
        str(getattr(decision, "ticker")),
        str(getattr(decision, "expiry")),
        float(getattr(decision, "short_strike")),
        float(getattr(decision, "long_strike")),
    )


def _resolve_approved_candidate(
    candidates: Sequence[CandidateSpread],
    approved_candidate_key: tuple[str, str, float, float],
) -> CandidateSpread | None:
    ticker, expiry, short_strike, long_strike = approved_candidate_key

    for candidate in candidates:
        if (
            candidate.ticker == ticker
            and candidate.expiry == expiry
            and _strikes_match(candidate.short_strike, short_strike)
            and _strikes_match(candidate.long_strike, long_strike)
        ):
            return candidate
    return None


def _decision_limit_credit(decision: Any) -> float:
    limit_credit = float(getattr(decision, "limit_credit"))
    if not math.isfinite(limit_credit) or limit_credit <= 0:
        raise ValueError("limit_credit must be a positive finite number")
    return limit_credit


def _strikes_match(candidate_strike: float | None, approved_strike: float) -> bool:
    return (
        candidate_strike is not None
        and math.isclose(float(candidate_strike), approved_strike, rel_tol=0.0, abs_tol=1e-9)
    )


def _error_result(*, status: str, reason: str, exc: Exception) -> dict[str, str]:
    return {
        "status": status,
        "reason": reason,
        "error_type": type(exc).__name__,
        "message": _exception_message(exc),
    }


def _exception_message(exc: Exception) -> str:
    message = str(exc)
    if message:
        return message
    if isinstance(exc, TimeoutError):
        return "IBKR request timed out"
    return type(exc).__name__


def build_cli_runtime() -> CliRuntime:
    config_dir = _default_config_dir()
    settings = load_settings(config_dir)
    market_data_client_id = settings.ibkr.client_id
    execution_client_id = market_data_client_id + 1
    market_data_client = IbkrMarketDataClient(
        host=settings.ibkr.host,
        port=settings.ibkr.port,
        client_id=market_data_client_id,
        market_data_type=settings.market_data_type,
        request_timeout_seconds=getattr(settings.ibkr, "request_timeout_seconds", 30),
    )
    earnings_calendar = EarningsCalendar(settings.events)
    executor = IbkrExecutor(
        host=settings.ibkr.host,
        port=settings.ibkr.port,
        client_id=execution_client_id,
    )
    audit_logger = AuditLogger(settings.audit_db_path)
    return CliRuntime(
        settings=settings,
        config_dir=config_dir,
        scanner=CliScanner(
            market_data_client=market_data_client,
            earnings_calendar=earnings_calendar,
            mode=settings.mode,
            candidate_filters=getattr(settings, "scan_filters", None),
        ),
        account_service=market_data_client,
        decision_service=_build_decision_service(settings),
        executor=executor,
        risk_guard=RiskGuard(settings.risk),
        position_service=market_data_client,
        position_manager=PositionManager(
            audit_logger=audit_logger,
            market_data=market_data_client,
            executor=executor,
            earnings_calendar=earnings_calendar,
            risk_settings=settings.risk,
            mode=settings.mode,
        ),
        audit_logger=audit_logger,
        dashboard_state_path=(config_dir.parent / "runtime" / "dashboard.json").resolve(),
    )


def _trade_cycle_command() -> dict[str, Any]:
    runtime, error = _load_command_runtime()
    if error is not None:
        return error
    try:
        result = _execute_entry_workflow("trade-cycle", runtime)
        return _with_dashboard_error(result, _update_dashboard_snapshot(runtime, result))
    finally:
        _disconnect_runtime(runtime)


def run_cli_command_with_runtime(
    command: str,
    runtime: CliRuntime,
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    scanner = getattr(runtime, "scanner", None)
    previous_runtime_progress = getattr(runtime, "progress_callback", None)
    previous_scanner_progress = getattr(scanner, "_progress_callback", None)
    runtime.progress_callback = progress_callback
    if hasattr(scanner, "set_progress_callback"):
        scanner.set_progress_callback(progress_callback)

    try:
        if command == "scan":
            result = _scan_command_with_runtime(runtime)
        elif command == "decide":
            result = _decide_command_with_runtime(runtime)
        elif command == "trade":
            result = _trade_command_with_runtime(runtime)
        elif command == "manage":
            result = _manage_command_with_runtime(runtime)
        else:
            raise ValueError(f"Unsupported command: {command}")
    finally:
        runtime.progress_callback = previous_runtime_progress
        if hasattr(scanner, "set_progress_callback"):
            scanner.set_progress_callback(previous_scanner_progress)

    return result


def _scan_command() -> dict[str, Any]:
    runtime, error = _load_command_runtime()
    if error is not None:
        return error
    try:
        return _scan_command_with_runtime(runtime)
    finally:
        _disconnect_runtime(runtime)


def _scan_command_with_runtime(runtime: CliRuntime) -> dict[str, Any]:
    scan_result, scan_error = _scan_market(runtime, command="scan")
    if scan_error is not None:
        return scan_error
    candidates = scan_result.candidates
    response = {
        **_command_envelope("scan", runtime=runtime),
        "status": "ok",
        "candidate_count": len(candidates),
        "candidates": _json_safe(candidates),
    }
    if runtime.settings.mode == "paper" and scan_result.watchlist:
        response["watchlist_count"] = len(scan_result.watchlist)
        response["watchlist"] = _json_safe(scan_result.watchlist)
    if scan_result.symbol_errors:
        response["symbol_error_count"] = len(scan_result.symbol_errors)
        response["symbol_errors"] = _json_safe(scan_result.symbol_errors)
    if scan_result.symbol_summaries:
        response["symbol_summaries"] = _json_safe(scan_result.symbol_summaries)
    return response


def _decide_command() -> dict[str, Any]:
    runtime, error = _load_command_runtime()
    if error is not None:
        return error
    try:
        return _decide_command_with_runtime(runtime)
    finally:
        _disconnect_runtime(runtime)


def _decide_command_with_runtime(runtime: CliRuntime) -> dict[str, Any]:
    candidates, candidate_error = _scan_candidates(runtime, command="decide")
    if candidate_error is not None:
        return candidate_error
    if not candidates:
        return {
            **_command_envelope("decide", runtime=runtime),
            "status": "no_candidates",
            "candidate_count": 0,
            "candidates": [],
        }

    decision, decision_error = _decide_on_candidates(runtime, candidates, command="decide")
    if decision_error is not None:
        return decision_error
    return {
        **_command_envelope("decide", runtime=runtime),
        "status": "ok",
        "candidate_count": len(candidates),
        "candidates": _json_safe(candidates),
        "decision": _json_safe(decision),
    }


def _trade_command() -> dict[str, Any]:
    runtime, error = _load_command_runtime()
    if error is not None:
        return error
    try:
        return _trade_command_with_runtime(runtime)
    finally:
        _disconnect_runtime(runtime)


def _trade_command_with_runtime(runtime: CliRuntime) -> dict[str, Any]:
    result = _execute_entry_workflow("trade", runtime)
    return _with_dashboard_error(result, _update_dashboard_snapshot(runtime, result))


def _manage_command() -> dict[str, Any]:
    runtime, error = _load_command_runtime()
    if error is not None:
        return error
    try:
        return _manage_command_with_runtime(runtime)
    finally:
        _disconnect_runtime(runtime)


def _manage_command_with_runtime(runtime: CliRuntime) -> dict[str, Any]:

    manager = getattr(runtime, "position_manager", None)
    manage_positions = _resolve_runtime_method(manager, "manage_positions")
    if manage_positions is None:
        response = _runtime_unavailable(
            "manage",
            runtime=runtime,
            reason="position_management_not_supported",
        )
        return _with_dashboard_error(
            response,
            _update_dashboard_snapshot(runtime, response),
        )

    try:
        result = manage_positions()
    except Exception as exc:
        uncertain_state = _detect_unresolved_uncertain_submission(
            runtime,
            event_type="close_submit_uncertain",
        )
        if uncertain_state is not None:
            response = {
                **_command_envelope("manage", runtime=runtime),
                **uncertain_state,
            }
            return _with_dashboard_error(
                response,
                _update_dashboard_snapshot(runtime, response),
            )
        response = _command_exception(
            "manage",
            runtime=runtime,
            status="manage_error",
            reason="position_management_failed",
            exc=exc,
        )
        return _with_dashboard_error(
            response,
            _update_dashboard_snapshot(runtime, response),
        )

    if not isinstance(result, dict):
        result = {"status": "ok", "payload": _json_safe(result)}
    response = {
        **_command_envelope("manage", runtime=runtime),
        **_json_safe(result),
    }
    return _with_dashboard_error(
        response,
        _update_dashboard_snapshot(runtime, response),
    )


def _dashboard_command(state_path: str | Path) -> dict[str, Any]:
    return read_dashboard_snapshot(state_path)


def _default_config_dir() -> Path:
    return (Path.cwd() / "config").resolve()


def _load_command_runtime() -> tuple[CliRuntime | None, dict[str, Any] | None]:
    config_dir = _default_config_dir()
    try:
        return build_cli_runtime(), None
    except Exception as exc:
        return None, {
            "status": "error",
            "reason": "config_load_failed",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "config_dir": str(config_dir),
        }


def _disconnect_runtime(runtime: CliRuntime) -> None:
    seen: set[int] = set()
    for service in (
        getattr(runtime, "scanner", None),
        getattr(runtime, "account_service", None),
        getattr(runtime, "position_service", None),
        getattr(runtime, "executor", None),
        getattr(runtime, "position_manager", None),
    ):
        _disconnect_service(service, seen=seen)


def _disconnect_service(service: Any, *, seen: set[int]) -> None:
    if service is None:
        return
    service_id = id(service)
    if service_id in seen:
        return
    seen.add(service_id)

    disconnect = getattr(service, "disconnect", None)
    if callable(disconnect):
        try:
            disconnect()
        except Exception:
            pass

    for attr_name in ("_market_data_client", "_executor", "_market_data"):
        child = getattr(service, attr_name, None)
        if child is not None:
            _disconnect_service(child, seen=seen)


def _command_envelope(
    command: str,
    *,
    runtime: CliRuntime,
) -> dict[str, Any]:
    return {
        "command": command,
        "mode": runtime.settings.mode,
        "live_enabled": runtime.settings.live_enabled,
        "config_dir": str(runtime.config_dir),
    }


def _build_decision_service(settings: AppSettings) -> AiDecisionService:
    primary = _build_ai_provider(
        settings.providers.primary_provider,
        timeout_seconds=settings.providers.provider_timeout_seconds,
    )
    secondary = _build_ai_provider(
        settings.providers.secondary_provider,
        timeout_seconds=settings.providers.secondary_timeout_seconds,
    )
    return AiDecisionService(primary=primary, secondary=secondary)


def _build_ai_provider(provider_name: str, *, timeout_seconds: int) -> Any:
    normalized = provider_name.strip().lower()
    if normalized == "claude_cli":
        return ClaudeCliAdapter(timeout_seconds=timeout_seconds)
    if normalized == "codex":
        return CodexAdapter(timeout_seconds=timeout_seconds)
    raise ValueError(f"unsupported AI provider: {provider_name}")


def _execute_entry_workflow(command: str, runtime: CliRuntime) -> dict[str, Any]:
    candidates, candidate_error = _scan_candidates(runtime, command=command)
    if candidate_error is not None:
        return candidate_error
    if not candidates:
        return {
            **_command_envelope(command, runtime=runtime),
            "status": "no_candidates",
        }

    if candidates:
        if getattr(runtime, "decision_service", None) is None:
            return _runtime_unavailable(command, runtime=runtime, reason="decision_service_unavailable")
        if getattr(runtime, "account_service", None) is None:
            return _runtime_unavailable(command, runtime=runtime, reason="account_service_unavailable")
        if getattr(runtime, "position_service", None) is None:
            return _runtime_unavailable(command, runtime=runtime, reason="position_service_unavailable")
        if getattr(runtime, "executor", None) is None:
            return _runtime_unavailable(command, runtime=runtime, reason="executor_unavailable")

    account, account_error = _fetch_account_snapshot(runtime, command=command)
    if account_error is not None:
        return account_error

    decision_result, matched_spread, limit_credit = _resolve_trade_decision(
        candidates=candidates,
        account=account,
        decision_service=runtime.decision_service,
    )
    if decision_result is not None:
        return {
            **_command_envelope(command, runtime=runtime),
            **_json_safe(decision_result),
        }

    open_symbol_count, position_error = _count_open_symbol_positions(
        runtime,
        ticker=matched_spread.ticker,
        command=command,
    )
    if position_error is not None:
        return position_error
    uncertain_open_state = _detect_unresolved_uncertain_open_submission(
        runtime,
        ticker=matched_spread.ticker,
    )
    if uncertain_open_state is not None:
        return {
            **_command_envelope(command, runtime=runtime),
            **uncertain_open_state,
        }
    pending_open_state = _detect_pending_open_submission(
        runtime,
        ticker=matched_spread.ticker,
    )
    if pending_open_state is not None:
        return {
            **_command_envelope(command, runtime=runtime),
            **pending_open_state,
        }

    if runtime.risk_guard is None:
        result = {"status": "risk_rejected", "reason": "risk_guard_missing"}
    else:
        guard_result = runtime.risk_guard.evaluate(
            matched_spread,
            account,
            open_symbol_count,
        )
        if not guard_result.allowed:
            result = {"status": "risk_rejected", "reason": guard_result.reason}
        else:
            try:
                result = runtime.executor.submit_open_credit_spread(
                    matched_spread,
                    limit_credit=limit_credit,
                    quantity=1,
                )
                audit_error = _persist_submitted_open_position(
                    runtime,
                    command=command,
                    spread=matched_spread,
                    limit_credit=limit_credit,
                    quantity=1,
                    submission=result,
                )
                if audit_error is not None:
                    result = {
                        **result,
                        "audit_error": audit_error,
                    }
            except OrderNotSubmittedError as exc:
                result = _error_result(
                    status="executor_error",
                    reason="submission_failed",
                    exc=exc,
                )
            except Exception as exc:
                result = _record_uncertain_open_submission(
                    runtime,
                    command=command,
                    spread=matched_spread,
                    limit_credit=limit_credit,
                    quantity=1,
                    exc=exc,
                )

    return {
        **_command_envelope(command, runtime=runtime),
        **_json_safe(result),
    }


def _scan_candidates(
    runtime: CliRuntime,
    *,
    command: str,
) -> tuple[list[CandidateSpread], dict[str, Any] | None]:
    scan_candidates = _resolve_runtime_method(
        getattr(runtime, "scanner", None),
        "scan_candidates",
        "scan_option_candidates",
    )
    if scan_candidates is None:
        scan_market = _resolve_runtime_method(
            getattr(runtime, "scanner", None),
            "scan_market",
        )
        if scan_market is None:
            return [], _runtime_unavailable(command, runtime=runtime, reason="scan_service_unavailable")
        try:
            market_scan = scan_market(list(runtime.settings.symbols))
        except Exception as exc:
            return [], _command_exception(
                command,
                runtime=runtime,
                status="scan_error",
                reason="scan_failed",
                exc=exc,
            )
        return list(_coerce_scan_market_result(market_scan).candidates), None

    try:
        candidates = scan_candidates(list(runtime.settings.symbols))
    except Exception as exc:
        return [], _command_exception(
            command,
            runtime=runtime,
            status="scan_error",
            reason="scan_failed",
            exc=exc,
        )

    return list(candidates), None


def _scan_market(
    runtime: CliRuntime,
    *,
    command: str,
) -> tuple[ScanMarketResult, dict[str, Any] | None]:
    scan_market = _resolve_runtime_method(
        getattr(runtime, "scanner", None),
        "scan_market",
    )
    if scan_market is None:
        candidates, candidate_error = _scan_candidates(runtime, command=command)
        return ScanMarketResult(candidates=list(candidates), watchlist=[]), candidate_error

    try:
        result = scan_market(list(runtime.settings.symbols))
    except Exception as exc:
        return ScanMarketResult(candidates=[], watchlist=[]), _command_exception(
            command,
            runtime=runtime,
            status="scan_error",
            reason="scan_failed",
            exc=exc,
        )

    return _coerce_scan_market_result(result), None


def _coerce_scan_market_result(result: Any) -> ScanMarketResult:
    if isinstance(result, ScanMarketResult):
        return ScanMarketResult(
            candidates=list(result.candidates),
            watchlist=list(result.watchlist),
            symbol_errors=list(result.symbol_errors),
            symbol_summaries=list(result.symbol_summaries),
        )

    if isinstance(result, Sequence) and not isinstance(
        result,
        (str, bytes, bytearray, dict),
    ):
        return ScanMarketResult(candidates=list(result), watchlist=[])

    return ScanMarketResult(
        candidates=list(getattr(result, "candidates", [])),
        watchlist=list(getattr(result, "watchlist", [])),
        symbol_errors=list(getattr(result, "symbol_errors", [])),
        symbol_summaries=list(getattr(result, "symbol_summaries", [])),
    )


def _decide_on_candidates(
    runtime: CliRuntime,
    candidates: Sequence[CandidateSpread],
    *,
    command: str,
) -> tuple[Any | None, dict[str, Any] | None]:
    decision_service = getattr(runtime, "decision_service", None)
    if decision_service is None:
        return None, _runtime_unavailable(command, runtime=runtime, reason="decision_service_unavailable")

    context = {
        "ticker": candidates[0].ticker,
        "candidate": candidates[0],
        "candidates": list(candidates),
    }
    try:
        return decision_service.decide(context), None
    except Exception as exc:
        return None, _command_exception(
            command,
            runtime=runtime,
            status="decision_error",
            reason="decision_service_failed",
            exc=exc,
        )


def _fetch_account_snapshot(
    runtime: CliRuntime,
    *,
    command: str,
) -> tuple[AccountSnapshot, dict[str, Any] | None]:
    fetch_account_snapshot = _resolve_runtime_method(
        getattr(runtime, "account_service", None),
        "fetch_account_snapshot",
        "get_account_snapshot",
    )
    if fetch_account_snapshot is None:
        return AccountSnapshot(), None

    try:
        return fetch_account_snapshot(), None
    except Exception as exc:
        return AccountSnapshot(), _command_exception(
            command,
            runtime=runtime,
            status="account_error",
            reason="account_snapshot_failed",
            exc=exc,
        )


def _count_open_symbol_positions(
    runtime: CliRuntime,
    *,
    ticker: str,
    command: str,
) -> tuple[int, dict[str, Any] | None]:
    if not ticker:
        return 0, None

    count_open_positions = _resolve_runtime_method(
        getattr(runtime, "position_service", None),
        "count_open_option_positions",
        "count_open_positions",
    )
    if count_open_positions is None:
        return 0, None

    try:
        return int(count_open_positions(ticker)), None
    except TypeError:
        try:
            return int(count_open_positions(symbol=ticker)), None
        except Exception as exc:
            return 0, _command_exception(
                command,
                runtime=runtime,
                status="position_error",
                reason="open_position_count_failed",
                exc=exc,
            )
    except Exception as exc:
        return 0, _command_exception(
            command,
            runtime=runtime,
            status="position_error",
            reason="open_position_count_failed",
            exc=exc,
        )


def _persist_submitted_open_position(
    runtime: CliRuntime,
    *,
    command: str,
    spread: CandidateSpread,
    limit_credit: float,
    quantity: int,
    submission: dict[str, Any],
) -> dict[str, str] | None:
    if submission.get("status") != "submitted":
        return None

    audit_logger = getattr(runtime, "audit_logger", None)
    upsert_managed_position = _resolve_runtime_method(
        audit_logger,
        "upsert_managed_position",
    )
    if upsert_managed_position is None:
        return None

    try:
        opened_at = datetime.now(UTC)
        position_id = _entry_position_id(command, submission)
        broker_fingerprint = str(
            submission.get("broker_fingerprint")
            or _entry_broker_fingerprint(spread, quantity=quantity)
        )
        upsert_managed_position(
            ManagedPositionRecord(
                position_id=position_id,
                ticker=spread.ticker,
                strategy=spread.strategy,
                expiry=spread.expiry,
                short_strike=float(spread.short_strike),
                long_strike=float(spread.long_strike),
                quantity=quantity,
                entry_credit=float(limit_credit),
                entry_order_id=_entry_order_id(submission),
                mode=runtime.settings.mode,
                status="opening",
                opened_at=opened_at,
                broker_fingerprint=broker_fingerprint,
                decision_reason=f"opened by {command}",
                risk_note="",
            )
        )

        record_position_event = _resolve_runtime_method(
            audit_logger,
            "record_position_event",
        )
        if record_position_event is None:
            return None

        record_position_event(
            position_id,
            "open_submitted",
            {
                "command": command,
                "entry_order_id": _entry_order_id(submission),
                "broker_fingerprint": broker_fingerprint,
            },
            created_at=opened_at,
        )
    except Exception as exc:
        return {
            "type": type(exc).__name__,
            "message": str(exc),
        }
    return None


def _record_uncertain_open_submission(
    runtime: CliRuntime,
    *,
    command: str,
    spread: CandidateSpread,
    limit_credit: float,
    quantity: int,
    exc: Exception,
) -> dict[str, Any]:
    audit_logger = getattr(runtime, "audit_logger", None)
    fingerprint = _entry_broker_fingerprint(spread, quantity=quantity)
    position_id = f"{command}-{uuid4().hex}"
    upsert_managed_position = _resolve_runtime_method(
        audit_logger,
        "upsert_managed_position",
    )
    audit_error: dict[str, str] | None = None

    if upsert_managed_position is not None:
        try:
            recorded_at = datetime.now(UTC)
            upsert_managed_position(
                ManagedPositionRecord(
                    position_id=position_id,
                    ticker=spread.ticker,
                    strategy=spread.strategy,
                    expiry=spread.expiry,
                    short_strike=float(spread.short_strike),
                    long_strike=float(spread.long_strike),
                    quantity=quantity,
                    entry_credit=float(limit_credit),
                    entry_order_id=None,
                    mode=runtime.settings.mode,
                    status="opening",
                    opened_at=recorded_at,
                    broker_fingerprint=fingerprint,
                    decision_reason=f"opened by {command}",
                    risk_note="manual intervention required",
                )
            )
            record_position_event = _resolve_runtime_method(
                audit_logger,
                "record_position_event",
            )
            if record_position_event is not None:
                record_position_event(
                    position_id,
                    "open_submit_uncertain",
                    {
                        "broker_fingerprint": fingerprint,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                    created_at=recorded_at,
                )
        except Exception as audit_exc:
            audit_error = {
                "type": type(audit_exc).__name__,
                "message": str(audit_exc),
            }

    result = {
        "status": "anomaly",
        "reason": "uncertain_submit_state",
        "fingerprints": [fingerprint],
        "manual_intervention_required": True,
    }
    if audit_error is not None:
        result["audit_error"] = audit_error
    return result


def _detect_unresolved_uncertain_open_submission(
    runtime: CliRuntime,
    *,
    ticker: str,
) -> dict[str, Any] | None:
    audit_state = _detect_unresolved_uncertain_submission(
        runtime,
        event_type="open_submit_uncertain",
        ticker=ticker,
    )
    if audit_state is not None:
        return audit_state
    return _detect_dashboard_uncertain_open_submission(runtime, ticker=ticker)


def _detect_pending_open_submission(
    runtime: CliRuntime,
    *,
    ticker: str,
) -> dict[str, Any] | None:
    return _detect_active_position_event(
        runtime,
        event_type="open_submitted",
        ticker=ticker,
        allowed_statuses={"opening"},
        reason="pending_open_submission",
        treat_missing_events_as_match=True,
    )


def _detect_unresolved_uncertain_submission(
    runtime: CliRuntime,
    *,
    event_type: str,
    ticker: str | None = None,
) -> dict[str, Any] | None:
    return _detect_active_position_event(
        runtime,
        event_type=event_type,
        ticker=ticker,
        allowed_statuses=None,
        reason="uncertain_submit_state",
        treat_missing_events_as_match=False,
    )


def _detect_dashboard_uncertain_open_submission(
    runtime: CliRuntime,
    *,
    ticker: str,
) -> dict[str, Any] | None:
    state_path = getattr(runtime, "dashboard_state_path", None)
    if state_path is None or not ticker:
        return None

    snapshot = read_dashboard_snapshot(state_path)
    last_cycle = snapshot.get("last_cycle")
    if not isinstance(last_cycle, dict):
        return None
    if last_cycle.get("status") != "anomaly":
        return None
    if last_cycle.get("reason") != "uncertain_submit_state":
        return None
    if last_cycle.get("manual_intervention_required") is not True:
        return None

    fingerprints = last_cycle.get("fingerprints")
    if not isinstance(fingerprints, list):
        return None

    matched_fingerprints = [
        fingerprint
        for fingerprint in fingerprints
        if isinstance(fingerprint, str) and _fingerprint_matches_ticker(fingerprint, ticker)
    ]
    if not matched_fingerprints:
        return None

    return {
        "status": "anomaly",
        "reason": "uncertain_submit_state",
        "fingerprints": matched_fingerprints,
        "manual_intervention_required": True,
    }


def _detect_active_position_event(
    runtime: CliRuntime,
    *,
    event_type: str,
    ticker: str | None,
    allowed_statuses: set[str] | None,
    reason: str,
    treat_missing_events_as_match: bool,
) -> dict[str, Any] | None:
    if ticker == "":
        return None
    if ticker is None:
        ticker = None

    audit_logger = getattr(runtime, "audit_logger", None)
    fetch_active_positions = _resolve_runtime_method(
        audit_logger,
        "fetch_active_managed_positions",
    )
    fetch_position_events = _resolve_runtime_method(
        audit_logger,
        "fetch_position_events",
    )
    if fetch_active_positions is None or fetch_position_events is None:
        return None

    fingerprints: list[str] = []
    try:
        active_positions = fetch_active_positions(mode=runtime.settings.mode)
        for position in active_positions:
            if ticker is not None and str(position.get("ticker", "")) != ticker:
                continue
            if allowed_statuses is not None and str(position.get("status", "")) not in allowed_statuses:
                continue
            events = fetch_position_events(str(position["position_id"]))
            if not events and treat_missing_events_as_match:
                fingerprints.append(str(position["broker_fingerprint"]))
                continue
            if events and events[-1].get("event_type") == event_type:
                fingerprints.append(str(position["broker_fingerprint"]))
                continue
            if _has_uncertain_submit_marker(position, event_type=event_type):
                fingerprints.append(str(position["broker_fingerprint"]))
    except Exception as exc:
        return {
            "status": "anomaly",
            "reason": "audit_lookup_failed",
            "manual_intervention_required": True,
            "audit_error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }

    if not fingerprints:
        return None

    return {
        "status": "anomaly",
        "reason": reason,
        "fingerprints": sorted(set(fingerprints)),
        "manual_intervention_required": True,
    }


def _has_uncertain_submit_marker(
    position: dict[str, Any],
    *,
    event_type: str,
) -> bool:
    if "uncertain" not in event_type:
        return False
    return str(position.get("risk_note") or "").strip() == "manual intervention required"


def _fingerprint_matches_ticker(fingerprint: str, ticker: str) -> bool:
    expected = str(ticker).strip().upper()
    actual = fingerprint.split("|", 1)[0].strip().upper()
    return bool(expected) and actual == expected


def _entry_position_id(command: str, submission: dict[str, Any]) -> str:
    order_id = _entry_order_id(submission)
    if order_id is not None:
        return f"{command}-{order_id}"
    return f"{command}-{uuid4().hex}"


def _entry_order_id(submission: dict[str, Any]) -> int | None:
    order_id = submission.get("order_id")
    if order_id in (None, ""):
        return None
    return int(order_id)


def _entry_broker_fingerprint(spread: CandidateSpread, *, quantity: int) -> str:
    return (
        f"{spread.ticker}|{spread.expiry}|"
        f"{_entry_option_right(spread.strategy)}|"
        f"{float(spread.short_strike)}|{float(spread.long_strike)}|"
        f"{int(quantity)}"
    )


def _entry_option_right(strategy: str) -> str:
    normalized = strategy.lower()
    if normalized == "bull_put_credit_spread":
        return "P"
    if normalized == "bear_call_credit_spread":
        return "C"
    raise ValueError(f"unsupported credit spread strategy: {strategy}")


def _resolve_runtime_method(service: Any, *names: str) -> Any | None:
    for name in names:
        method = getattr(service, name, None)
        if callable(method):
            return method
    return None


def _runtime_unavailable(
    command: str,
    *,
    runtime: CliRuntime,
    reason: str,
) -> dict[str, Any]:
    return {
        **_command_envelope(command, runtime=runtime),
        "status": "runtime_unavailable",
        "reason": reason,
    }


def _command_exception(
    command: str,
    *,
    runtime: CliRuntime,
    status: str,
    reason: str,
    exc: Exception,
) -> dict[str, Any]:
    return {
        **_command_envelope(command, runtime=runtime),
        **_error_result(status=status, reason=reason, exc=exc),
    }


def _update_dashboard_snapshot(
    runtime: CliRuntime,
    result: dict[str, Any],
) -> dict[str, str] | None:
    state_path = getattr(runtime, "dashboard_state_path", None)
    if state_path is None:
        return None

    try:
        update_dashboard_state(state_path, last_cycle=result)
    except Exception as exc:
        return {
            "type": type(exc).__name__,
            "message": str(exc),
        }
    return None


def _with_dashboard_error(
    result: dict[str, Any],
    dashboard_error: dict[str, str] | None,
) -> dict[str, Any]:
    if dashboard_error is None:
        return result
    return {
        **result,
        "dashboard_error": dashboard_error,
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            item.name: _json_safe(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]

    public_attrs = {
        name: _json_safe(getattr(value, name))
        for name in dir(value)
        if not name.startswith("_")
        and not callable(getattr(value, name))
    }
    if public_attrs:
        return public_attrs
    return value


def _callable_accepts_keyword(func: Callable[..., Any], name: str) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == name:
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
