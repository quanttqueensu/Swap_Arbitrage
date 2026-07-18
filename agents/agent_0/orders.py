from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .models import QueuedOrder


ORDER_COLUMNS = [
    "order_ref",
    "activate_at",
    "symbol",
    "side",
    "quantity",
    "status",
    "contract_id",
    "order_id",
]


def build_order(account_id: str, queued_order: QueuedOrder) -> Any:
    if queued_order.side not in {"BUY", "SELL"}:
        raise ValueError("Order side must be BUY or SELL.")

    if queued_order.quantity <= 0:
        raise ValueError("Cannot build order without a positive quantity.")

    if queued_order.activate_at.utcoffset() is None:
        raise ValueError("Order activation time must include a timezone.")

    try:
        from ib_insync import MarketOrder
    except ImportError as exc:
        raise ImportError(
            "Missing dependency: ib_insync. Install it with:\n\n"
            "pip install ib_insync\n"
        ) from exc

    order = MarketOrder(queued_order.side, queued_order.quantity)
    order.account = account_id
    order.tif = "DAY"
    order.transmit = True
    order.orderRef = queued_order.order_ref
    order.goodAfterTime = queued_order.activate_at.astimezone(timezone.utc).strftime(
        "%Y%m%d-%H:%M:%S"
    )

    return order


def load_orders(path: Path) -> list[QueuedOrder]:
    if not path.exists():
        return []

    with path.open(newline="", encoding="utf-8") as handle:
        return [
            QueuedOrder(
                order_ref=row["order_ref"],
                activate_at=datetime.fromisoformat(row["activate_at"]),
                symbol=row["symbol"],
                side=row["side"],
                quantity=int(row["quantity"]),
                status=row["status"],
                contract_id=row["contract_id"],
                order_id=row["order_id"],
            )
            for row in csv.DictReader(handle)
        ]


def save_orders(path: Path, rows: list[QueuedOrder]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")

    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ORDER_COLUMNS)
        writer.writeheader()
        writer.writerows(
            {
                "order_ref": row.order_ref,
                "activate_at": row.activate_at.isoformat(),
                "symbol": row.symbol,
                "side": row.side,
                "quantity": row.quantity,
                "status": row.status,
                "contract_id": row.contract_id,
                "order_id": row.order_id,
            }
            for row in rows
        )

    temporary.replace(path)


def roll_tracking(
    now: datetime,
    upcoming_path: Path = config.UPCOMING_ORDERS_FILE,
    previous_path: Path = config.PREVIOUS_ORDERS_FILE,
) -> None:
    if now.utcoffset() is None:
        raise ValueError("Tracking time must include a timezone.")

    upcoming = load_orders(upcoming_path)
    previous = load_orders(previous_path)
    still_upcoming = []

    for row in upcoming:
        if row.activate_at <= now:
            previous.append(row)
        else:
            still_upcoming.append(row)

    save_orders(upcoming_path, still_upcoming)
    save_orders(previous_path, previous)


def trade_order_id(trade: Any) -> str:
    order = getattr(trade, "order", None)
    order_id = getattr(order, "orderId", "")
    return str(order_id)


def trade_status(trade: Any) -> str:
    order_status = getattr(trade, "orderStatus", None)
    status = getattr(order_status, "status", "")
    return str(status)
