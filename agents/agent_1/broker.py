from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import (
    BoundContract, BrokerSnapshot, PositionAuditSnapshot, QuoteSnapshot, WorkingOrderSnapshot,
)


ORDER_REF_PREFIX = "A1:"
DELAYED_MARKET_DATA_TYPE = 3


def is_agent1_trade(trade: Any, *, account_id: str, client_id: int) -> bool:
    order = getattr(trade, "order", None)
    if order is None:
        return False

    order_ref = str(getattr(order, "orderRef", "") or "")
    order_account = str(getattr(order, "account", "") or "")
    order_client_id = getattr(order, "clientId", None)

    return (
        order_ref.startswith(ORDER_REF_PREFIX)
        and order_account == account_id
        and type(order_client_id) is int
        and order_client_id == client_id
    )


def cancel_agent1_orders(ib: Any, *, account_id: str, client_id: int) -> tuple[int, ...]:
    cancelled: list[int] = []
    for trade in list(ib.reqAllOpenOrders()):
        if not is_agent1_trade(trade, account_id=account_id, client_id=client_id):
            continue
        order = getattr(trade, "order", None)
        order_id = getattr(order, "orderId", None)
        if type(order_id) is not int or order_id <= 0:
            continue
        ib.cancelOrder(order)
        cancelled.append(order_id)
    return tuple(cancelled)


def cancel_group_orders(
    ib: Any,
    *,
    group_id: str,
    account_id: str,
    client_id: int,
) -> tuple[int, ...]:
    """Cancel only working orders that belong to one Agent 1 order group."""
    if not isinstance(group_id, str) or not group_id.startswith(ORDER_REF_PREFIX):
        raise ValueError("Agent 1 group ID is invalid.")
    prefix = f"{group_id}:"
    cancelled: list[int] = []
    for trade in list(ib.reqAllOpenOrders()):
        if not is_agent1_trade(trade, account_id=account_id, client_id=client_id):
            continue
        order = getattr(trade, "order", None)
        order_ref = str(getattr(order, "orderRef", "") or "")
        if not order_ref.startswith(prefix):
            continue
        order_id = getattr(order, "orderId", None)
        if type(order_id) is not int or order_id <= 0:
            continue
        ib.cancelOrder(order)
        cancelled.append(order_id)
    return tuple(cancelled)


def margin_reserve_ok(order_state: Any, reserve_fraction: Decimal) -> bool:
    if (
        type(reserve_fraction) is not Decimal
        or not reserve_fraction.is_finite()
        or reserve_fraction <= 0
        or reserve_fraction > 1
    ):
        return False
    try:
        equity = Decimal(str(order_state.equityWithLoanAfter))
        initial_margin = Decimal(str(order_state.initMarginAfter))
    except (AttributeError, InvalidOperation, ValueError):
        return False
    if not equity.is_finite() or not initial_margin.is_finite() or equity <= 0 or initial_margin < 0:
        return False
    return equity - initial_margin >= reserve_fraction * equity


def preview_margin(
    ib: Any,
    *,
    contract: Any,
    order: Any,
    reserve_fraction: Decimal,
) -> bool:
    try:
        state = ib.whatIfOrder(contract, order)
    except Exception:
        return False
    return margin_reserve_ok(state, reserve_fraction)


class BrokerError(RuntimeError):
    """Raised when broker truth cannot be read safely."""


def request_delayed_market_data(ib: Any) -> None:
    """Request IBKR's delayed-data fallback for this paper session."""
    try:
        ib.reqMarketDataType(DELAYED_MARKET_DATA_TYPE)
    except Exception as exc:
        raise BrokerError("Could not request IBKR delayed market data.") from exc


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
) -> tuple[dict[int, int], dict[int, PositionAuditSnapshot]]:
    positions = {con_id: 0 for con_id in tracked_con_ids}
    details: dict[int, PositionAuditSnapshot] = {}

    portfolio = getattr(ib, "portfolio", None)
    if callable(portfolio):
        try:
            rows = list(portfolio(account_id))
        except Exception as exc:
            raise BrokerError("Could not read IBKR portfolio.") from exc
        for row in rows:
            item_account = str(getattr(row, "account", "") or "")
            if item_account and item_account != account_id:
                continue
            contract = getattr(row, "contract", None)
            con_id = getattr(contract, "conId", None)
            if type(con_id) is not int or con_id not in tracked_con_ids:
                continue
            quantity = _integer_quantity(getattr(row, "position", None), "position")
            positions[con_id] = quantity
            if quantity == 0:
                continue
            average_cost = _decimal(getattr(row, "averageCost", getattr(row, "avgCost", None)))
            market_price = _decimal(getattr(row, "marketPrice", None))
            unrealized = _decimal(getattr(row, "unrealizedPNL", None))
            realized = _decimal(getattr(row, "realizedPNL", None))
            if (
                average_cost is not None and average_cost > 0
                and market_price is not None and market_price > 0
                and unrealized is not None and realized is not None
            ):
                details[con_id] = PositionAuditSnapshot(
                    quantity=quantity,
                    average_cost=average_cost,
                    market_price=market_price,
                    unrealized_pnl_usd=unrealized,
                    realized_pnl_usd=realized,
                )
        return positions, details

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
    return positions, details


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
        bid_size = _decimal(getattr(ticker, "bidSize", None))
        ask_size = _decimal(getattr(ticker, "askSize", None))
        quotes[con_id] = QuoteSnapshot(
            con_id=con_id, bid=bid, ask=ask, timestamp=timestamp,
            bid_size=bid_size if bid_size is not None and bid_size > 0 else None,
            ask_size=ask_size if ask_size is not None and ask_size > 0 else None,
        )
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

    positions, position_details = _collect_positions(
        ib,
        account_id=account_id,
        tracked_con_ids=tracked_con_ids,
    )
    return BrokerSnapshot(
        observed_at=observed_at,
        positions=positions,
        working_orders=_collect_working_orders(
            ib,
            account_id=account_id,
            client_id=client_id,
            tracked_con_ids=tracked_con_ids,
        ),
        quotes=_collect_quotes(ib, bindings=bindings),
        position_details=position_details,
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
