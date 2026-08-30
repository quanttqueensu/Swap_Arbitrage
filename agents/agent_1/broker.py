from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .broker_scope import is_agent1_trade
from .models import BoundContract, BrokerSnapshot, QuoteSnapshot, WorkingOrderSnapshot


class BrokerError(RuntimeError):
    """Raised when broker truth cannot be read safely."""


@dataclass(frozen=True)
class QuoteAssessment:
    data_fresh: bool
    bid_ask_valid: bool
    market_fields_valid: bool


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _integer_quantity(value: object, label: str) -> int:
    parsed = _decimal(value)
    if parsed is None or parsed != parsed.to_integral_value():
        raise BrokerError(f"Tracked futures {label} must be an integer quantity.")
    return int(parsed)


def validate_paper_session(ib: Any, *, account_id: str) -> None:
    if not str(account_id).upper().startswith("DU"):
        raise BrokerError("Agent 1 requires a DU paper account.")
    try:
        connected = bool(ib.isConnected())
    except Exception as exc:
        raise BrokerError("Could not verify IBKR connection state.") from exc
    if not connected:
        raise BrokerError("IBKR paper session is disconnected.")
    try:
        accounts = list(ib.managedAccounts())
    except Exception as exc:
        raise BrokerError("Could not read IBKR managed accounts.") from exc
    if account_id not in accounts:
        raise BrokerError(
            f"Configured paper account {account_id!r} is not a managed account in this session."
        )


def _collect_positions(
    ib: Any,
    *,
    account_id: str,
    tracked_con_ids: set[int],
) -> dict[int, int]:
    positions = {con_id: 0 for con_id in tracked_con_ids}
    try:
        rows = list(ib.positions())
    except Exception as exc:
        raise BrokerError("Could not read IBKR positions.") from exc

    for row in rows:
        if str(getattr(row, "account", "")) != account_id:
            continue
        contract = getattr(row, "contract", None)
        con_id = getattr(contract, "conId", None)
        if type(con_id) is not int or con_id not in tracked_con_ids:
            continue
        positions[con_id] = _integer_quantity(getattr(row, "position", None), "position")
    return positions


def _collect_working_orders(
    ib: Any,
    *,
    account_id: str,
    client_id: int,
    tracked_con_ids: set[int],
) -> tuple[WorkingOrderSnapshot, ...]:
    try:
        trades = list(ib.reqAllOpenOrders())
    except Exception as exc:
        raise BrokerError("Could not read IBKR open orders.") from exc

    rows: list[WorkingOrderSnapshot] = []
    for trade in trades:
        if not is_agent1_trade(trade, account_id=account_id, client_id=client_id):
            continue
        contract = getattr(trade, "contract", None)
        con_id = getattr(contract, "conId", None)
        if type(con_id) is not int or con_id not in tracked_con_ids:
            continue
        order = getattr(trade, "order", None)
        status_obj = getattr(trade, "orderStatus", None)
        status = str(getattr(status_obj, "status", "") or "")
        if status in {"Cancelled", "ApiCancelled", "Filled", "Inactive"}:
            continue
        remaining = _integer_quantity(getattr(status_obj, "remaining", None), "working order")
        if remaining <= 0:
            continue
        action = str(getattr(order, "action", "") or "").upper()
        if action not in {"BUY", "SELL"}:
            raise BrokerError("Agent 1 working order has invalid action.")
        signed = remaining if action == "BUY" else -remaining
        order_id = getattr(order, "orderId", None)
        if type(order_id) is not int or order_id <= 0:
            raise BrokerError("Agent 1 working order has invalid order ID.")
        rows.append(
            WorkingOrderSnapshot(
                order_ref=str(getattr(order, "orderRef", "")),
                order_id=order_id,
                con_id=con_id,
                signed_remaining_qty=signed,
                status=status,
            )
        )
    return tuple(rows)


def _collect_quotes(
    ib: Any,
    *,
    bindings: dict[str, BoundContract],
) -> dict[int, QuoteSnapshot]:
    contracts = []
    for binding in bindings.values():
        if binding.broker_contract is None:
            raise BrokerError(f"Missing qualified broker contract for conId {binding.con_id}.")
        contracts.append(binding.broker_contract)
    try:
        tickers = list(ib.reqTickers(*contracts))
    except Exception as exc:
        raise BrokerError("Could not request IBKR bid/ask quotes.") from exc

    quotes: dict[int, QuoteSnapshot] = {}
    for ticker in tickers:
        contract = getattr(ticker, "contract", None)
        con_id = getattr(contract, "conId", None)
        if type(con_id) is not int:
            continue
        bid = _decimal(getattr(ticker, "bid", None))
        ask = _decimal(getattr(ticker, "ask", None))
        timestamp = getattr(ticker, "time", None)
        if type(timestamp) is not datetime or timestamp.utcoffset() is None:
            # Keep an invalid quote out of the map; assessment will mark fields invalid.
            continue
        if bid is None or ask is None:
            continue
        quotes[con_id] = QuoteSnapshot(con_id=con_id, bid=bid, ask=ask, timestamp=timestamp)
    return quotes


def collect_broker_snapshot(
    ib: Any,
    *,
    account_id: str,
    client_id: int,
    bindings: dict[str, BoundContract],
    observed_at: datetime,
) -> BrokerSnapshot:
    if observed_at.utcoffset() is None:
        raise BrokerError("Broker snapshot time must include a timezone.")
    validate_paper_session(ib, account_id=account_id)
    tracked_con_ids = {binding.con_id for binding in bindings.values()}
    if not tracked_con_ids:
        raise BrokerError("At least one bound contract is required for a broker snapshot.")

    return BrokerSnapshot(
        observed_at=observed_at,
        positions=_collect_positions(
            ib,
            account_id=account_id,
            tracked_con_ids=tracked_con_ids,
        ),
        working_orders=_collect_working_orders(
            ib,
            account_id=account_id,
            client_id=client_id,
            tracked_con_ids=tracked_con_ids,
        ),
        quotes=_collect_quotes(ib, bindings=bindings),
    )


def assess_quotes(
    snapshot: BrokerSnapshot,
    *,
    now: datetime,
    max_age_seconds: Decimal,
) -> QuoteAssessment:
    if now.utcoffset() is None:
        raise BrokerError("Quote assessment time must include a timezone.")
    if type(max_age_seconds) is not Decimal or not max_age_seconds.is_finite() or max_age_seconds <= 0:
        raise BrokerError("max_age_seconds must be a positive finite Decimal.")

    market_fields_valid = True
    bid_ask_valid = True
    data_fresh = True

    if not snapshot.quotes:
        return QuoteAssessment(False, False, False)

    for quote in snapshot.quotes.values():
        bid = _decimal(getattr(quote, "bid", None))
        ask = _decimal(getattr(quote, "ask", None))
        timestamp = getattr(quote, "timestamp", None)
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            market_fields_valid = False
            bid_ask_valid = False
        elif bid > ask:
            bid_ask_valid = False
        if type(timestamp) is not datetime or timestamp.utcoffset() is None:
            market_fields_valid = False
            data_fresh = False
        else:
            age = Decimal(str((now - timestamp).total_seconds()))
            if age < 0 or age > max_age_seconds:
                data_fresh = False

    return QuoteAssessment(
        data_fresh=data_fresh,
        bid_ask_valid=bid_ask_valid,
        market_fields_valid=market_fields_valid,
    )


def _default_ib_factory() -> Any:
    try:
        from ib_insync import IB
    except ImportError as exc:
        raise BrokerError("ib_insync is required for IBKR paper connectivity.") from exc
    return IB()


def connect_paper(
    config: object,
    *,
    ib_factory: Any | None = None,
    timeout_seconds: int = 20,
) -> Any:
    host = getattr(config, "host", None)
    port = getattr(config, "port", None)
    account = getattr(config, "account", None)
    client_id = getattr(config, "client_id", None)
    if host != "127.0.0.1" or port != 7497:
        raise BrokerError("Agent 1 requires localhost IBKR paper endpoint 127.0.0.1:7497.")
    if type(client_id) is not int or client_id <= 0 or client_id == 30:
        raise BrokerError("Agent 1 requires a positive client ID distinct from Agent 0.")
    if not str(account).upper().startswith("DU"):
        raise BrokerError("Agent 1 requires a DU paper account.")
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise BrokerError("IBKR timeout must be a positive integer.")

    ib = (ib_factory or _default_ib_factory)()
    try:
        ib.connect(
            host=host,
            port=port,
            clientId=client_id,
            timeout=timeout_seconds,
        )
        validate_paper_session(ib, account_id=str(account))
        return ib
    except Exception:
        try:
            if bool(ib.isConnected()):
                ib.disconnect()
        except Exception:
            pass
        raise


def disconnect(ib: Any) -> None:
    if ib is None:
        return
    try:
        if bool(ib.isConnected()):
            ib.disconnect()
    except Exception as exc:
        raise BrokerError("Could not disconnect Agent 1 IBKR session cleanly.") from exc
