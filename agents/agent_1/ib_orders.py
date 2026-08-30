from __future__ import annotations

from typing import Any, Callable

from .order_planning import LimitOrderPlan


def _default_order_factory(action: str, quantity: int, limit_price: float) -> Any:
    try:
        from ib_insync import LimitOrder
    except ImportError as exc:
        raise ImportError("ib_insync is required to build IBKR limit orders.") from exc
    return LimitOrder(action, quantity, limit_price)


def build_ib_limit_order(
    account_id: str,
    plan: LimitOrderPlan,
    *,
    order_factory: Callable[[str, int, float], Any] | None = None,
) -> Any:
    if not str(account_id).upper().startswith("DU"):
        raise ValueError("Agent 1 limit orders require a DU paper account.")
    if type(plan) is not LimitOrderPlan:
        raise ValueError("Agent 1 requires a validated LimitOrderPlan.")
    if not plan.order_ref.startswith("A1:"):
        raise ValueError("Agent 1 order references must start with A1:.")
    if plan.order_type != "LMT":
        raise ValueError("Agent 1 never builds non-limit orders.")

    factory = order_factory or _default_order_factory
    order = factory(plan.side, plan.quantity, float(plan.limit_price))
    order.account = account_id
    order.tif = "DAY"
    order.transmit = True
    order.orderRef = plan.order_ref
    return order
