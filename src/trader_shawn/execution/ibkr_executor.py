from __future__ import annotations

from importlib import import_module
from typing import Any

from trader_shawn.domain.models import CandidateSpread, PositionSnapshot
from trader_shawn.execution.order_builder import (
    build_credit_spread_combo_order,
    build_open_credit_spread_combo_order,
)


class OrderNotSubmittedError(RuntimeError):
    """Raised when local pre-submit work fails before IBKR receives the order."""


class IbkrExecutor:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 7,
        client: Any | None = None,
        ib: Any | None = None,
        ibkr_module: Any | None = None,
        ib_api: Any | None = None,
        client_factory: Any | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._client = client if client is not None else ib
        self._ibkr_module = ibkr_module if ibkr_module is not None else ib_api
        self._client_factory = client_factory

    def submit_open_credit_spread(
        self,
        spread: CandidateSpread,
        *,
        limit_credit: float,
        quantity: int = 1,
    ) -> dict[str, object]:
        try:
            payload = build_open_credit_spread_combo_order(
                spread,
                limit_credit=limit_credit,
                quantity=quantity,
            )
        except Exception as exc:
            raise OrderNotSubmittedError(str(exc)) from exc
        return self._submit_combo_order(payload)

    def submit_limit_combo(
        self,
        position: PositionSnapshot,
        *,
        limit_price: float,
    ) -> dict[str, object]:
        try:
            payload = build_credit_spread_combo_order(
                position,
                limit_price=limit_price,
            )
        except Exception as exc:
            raise OrderNotSubmittedError(str(exc)) from exc
        submission = self._submit_combo_order(payload)
        submission["broker_fingerprint"] = self._broker_fingerprint(position)
        return submission

    def _submit_combo_order(self, payload: dict[str, object]) -> dict[str, object]:
        client = self._ensure_connected()
        ibkr = self._resolve_ibkr_module()
        symbol = str(payload["symbol"])
        exchange = str(payload["exchange"])
        currency = str(payload["currency"])
        leg_payloads = list(payload["legs"])

        try:
            qualified_legs = list(
                client.qualifyContracts(
                    *[
                        ibkr.Option(
                            symbol=symbol,
                            lastTradeDateOrContractMonth=_ibkr_expiry(
                                str(leg["expiry"])
                            ),
                            strike=float(leg["strike"]),
                            right=str(leg["right"]),
                            exchange=exchange,
                            currency=currency,
                        )
                        for leg in leg_payloads
                    ]
                )
            )
            if len(qualified_legs) != len(leg_payloads):
                raise OrderNotSubmittedError("failed to qualify all combo legs")

            combo_legs: list[Any] = []
            normalized_legs: list[dict[str, object]] = []
            for contract, leg in zip(qualified_legs, leg_payloads, strict=True):
                con_id = int(getattr(contract, "conId", 0))
                if con_id <= 0:
                    raise OrderNotSubmittedError("qualified combo leg is missing conId")
                combo_legs.append(
                    ibkr.ComboLeg(
                        conId=con_id,
                        ratio=int(leg["ratio"]),
                        action=str(leg["action"]),
                        exchange=exchange,
                    )
                )
                normalized_legs.append(
                    {
                        "con_id": con_id,
                        "action": str(leg["action"]),
                        "ratio": int(leg["ratio"]),
                        "exchange": exchange,
                        "right": str(leg["right"]),
                        "strike": float(leg["strike"]),
                        "expiry": str(leg["expiry"]),
                    }
                )

            bag_contract = ibkr.Contract(
                symbol=symbol,
                secType="BAG",
                currency=currency,
                exchange=exchange,
                comboLegs=combo_legs,
            )
            order_payload = dict(payload["order"])
            order = ibkr.LimitOrder(
                str(order_payload["action"]),
                int(order_payload["totalQuantity"]),
                float(order_payload["lmtPrice"]),
                transmit=True,
            )
        except OrderNotSubmittedError:
            raise
        except Exception as exc:
            raise OrderNotSubmittedError(str(exc)) from exc

        trade = client.placeOrder(bag_contract, order)

        return {
            "status": "submitted",
            "broker": "ibkr",
            "order_id": _extract_order_id(trade, order),
            "order": {
                "action": str(getattr(order, "action", order_payload["action"])),
                "orderType": str(getattr(order, "orderType", "LMT")),
                "totalQuantity": int(
                    getattr(order, "totalQuantity", order_payload["totalQuantity"])
                ),
                "lmtPrice": float(getattr(order, "lmtPrice", order_payload["lmtPrice"])),
                "transmit": bool(getattr(order, "transmit", True)),
            },
            "contract": {
                "symbol": symbol,
                "secType": "BAG",
                "currency": currency,
                "exchange": exchange,
            },
            "legs": normalized_legs,
            "broker_status": _extract_broker_status(trade),
        }

    def _ensure_connected(self) -> Any:
        client = self._resolve_client()
        is_connected = getattr(client, "isConnected", None)
        if callable(is_connected) and is_connected():
            return client

        connect = getattr(client, "connect", None)
        if callable(connect):
            connect(self._host, self._port, clientId=self._client_id)
        return client

    def _resolve_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory()
            return self._client
        self._client = self._resolve_ibkr_module().IB()
        return self._client

    def _resolve_ibkr_module(self) -> Any:
        if self._ibkr_module is None:
            self._ibkr_module = import_module("ib_insync")
        return self._ibkr_module

    def _broker_fingerprint(self, position: PositionSnapshot) -> str:
        right = _option_right_for_strategy(position.strategy)
        return (
            f"{position.ticker}|{position.expiry}|{right}|"
            f"{float(position.short_strike)}|{float(position.long_strike)}|"
            f"{int(position.quantity)}"
        )


def _extract_order_id(trade: Any, order: Any) -> int | None:
    for candidate in (getattr(order, "orderId", None), getattr(getattr(trade, "order", None), "orderId", None)):
        if candidate in (None, 0):
            continue
        return int(candidate)
    return None


def _extract_broker_status(trade: Any) -> str | None:
    order_status = getattr(trade, "orderStatus", None)
    if order_status is None:
        return None
    status = getattr(order_status, "status", None)
    return None if status is None else str(status)


def _ibkr_expiry(expiry: str) -> str:
    return expiry.replace("-", "")


def _option_right_for_strategy(strategy: str) -> str:
    normalized = strategy.lower()
    if normalized == "bull_put_credit_spread":
        return "P"
    if normalized == "bear_call_credit_spread":
        return "C"
    raise ValueError(f"unsupported credit spread strategy: {strategy}")
