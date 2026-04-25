from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from trader_shawn.monitoring.quote_snapshot_store import QuoteSnapshotStore


def collect_quote_snapshots(
    *,
    market_data_client: Any,
    store: QuoteSnapshotStore,
    symbols: Iterable[str],
    scan_inputs: Any | None = None,
    collected_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = collected_at or datetime.now(UTC)
    fetch_option_quotes = getattr(market_data_client, "fetch_option_quotes", None)
    if not callable(fetch_option_quotes):
        return {
            "status": "error",
            "reason": "market_data_unavailable",
            "message": "market data client does not expose fetch_option_quotes",
            "symbol_count": 0,
            "quote_count": 0,
            "snapshot_count": 0,
            "symbol_errors": [],
        }

    market_data_type = str(
        getattr(
            market_data_client,
            "market_data_type",
            getattr(market_data_client, "_market_data_type", "unknown"),
        )
    )
    scan_kwargs = _scan_input_kwargs(scan_inputs, fetch_option_quotes)
    symbol_errors: list[dict[str, str]] = []
    quote_count = 0
    snapshot_count = 0
    collected_symbols = 0

    for symbol in symbols:
        collected_symbols += 1
        try:
            quotes = list(fetch_option_quotes(symbol, **scan_kwargs))
        except Exception as exc:
            symbol_errors.append(
                {
                    "symbol": symbol,
                    "error_type": type(exc).__name__,
                    "message": str(exc) or type(exc).__name__,
                }
            )
            continue
        store.record_symbol_quotes(
            symbol,
            quotes,
            market_data_type=market_data_type,
            scan_inputs=scan_kwargs,
            collected_at=timestamp,
        )
        quote_count += len(quotes)
        snapshot_count += 1

    return {
        "status": "partial" if symbol_errors else "ok",
        "symbol_count": collected_symbols,
        "quote_count": quote_count,
        "snapshot_count": snapshot_count,
        "symbol_error_count": len(symbol_errors),
        "symbol_errors": symbol_errors,
        "market_data_type": market_data_type,
        "collected_at": timestamp.isoformat(),
    }


def _scan_input_kwargs(scan_inputs: Any | None, func: Callable[..., Any]) -> dict[str, Any]:
    if scan_inputs is None:
        return {}
    kwargs: dict[str, Any] = {}
    for name in (
        "min_dte",
        "max_dte",
        "strike_window_pct",
        "fallback_strike_count",
        "max_expiries",
    ):
        if not _callable_accepts_keyword(func, name):
            continue
        value = getattr(scan_inputs, name, None)
        if value is not None:
            kwargs[name] = value
    return kwargs


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
