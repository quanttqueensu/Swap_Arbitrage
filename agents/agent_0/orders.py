from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import config
from .models import TradeDecision


def build_order(account_id: str, decision: TradeDecision) -> Any:
    if decision.action not in {"enter", "flatten"}:
        raise ValueError(f"Cannot build order for decision action {decision.action!r}.")

    if decision.side is None:
        raise ValueError("Cannot build order without a side.")

    if decision.quantity <= 0:
        raise ValueError("Cannot build order without a positive quantity.")

    if config.DEFAULT_ORDER_TYPE != "MKT":
        raise ValueError(
            f"Unsupported Agent 0 order type: {config.DEFAULT_ORDER_TYPE!r}."
        )

    try:
        from ib_insync import MarketOrder
    except ImportError as exc:
        raise ImportError(
            "Missing dependency: ib_insync. Install it with:\n\n"
            "pip install ib_insync\n"
        ) from exc

    order = MarketOrder(decision.side, decision.quantity)
    order.account = account_id
    order.tif = config.ORDER_TIF
    order.orderRef = (
        f"{config.ORDER_REF_PREFIX}-{decision.action}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )

    return order


def trade_order_id(trade: Any) -> str:
    order = getattr(trade, "order", None)
    order_id = getattr(order, "orderId", "")
    return str(order_id)


def trade_status(trade: Any) -> str:
    order_status = getattr(trade, "orderStatus", None)
    status = getattr(order_status, "status", "")
    return str(status)
