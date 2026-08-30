from __future__ import annotations

from typing import Any


ORDER_REF_PREFIX = "A1:"


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
