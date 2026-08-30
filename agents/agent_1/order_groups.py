from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR, localcontext
from typing import Literal

from .models import BoundContract, MaturityReconciliation, PositionState, QuoteSnapshot
from .order_planning import LimitOrderPlan, build_leg_order


RecoveryAction = Literal["wait", "hedge", "flatten", "complete"]


@dataclass(frozen=True)
class OrderGroupPlan:
    group_id: str
    maturity: str
    target_version: str
    phase: str
    created_at: datetime
    expires_at: datetime
    start_swap_qty: int
    start_treasury_qty: int
    requested_swap_delta: int
    requested_treasury_delta: int
    orders: tuple[LimitOrderPlan, ...]


@dataclass(frozen=True)
class PartialFillRecovery:
    action: RecoveryAction
    swap_delta: int
    treasury_delta: int


def _target_digest(version: str) -> str:
    digest = version.rsplit(":", 1)[-1].strip()
    return digest[:12] if digest else "no-version"


def build_order_group(
    *,
    maturity: str,
    target_version: str,
    sequence: int,
    reconciliation: MaturityReconciliation,
    swap_state: PositionState,
    treasury_state: PositionState,
    bindings: dict[str, BoundContract],
    quotes: dict[str, QuoteSnapshot],
    created_at: datetime,
    timeout_seconds: Decimal,
) -> OrderGroupPlan | None:
    if reconciliation.is_noop:
        return None
    if type(sequence) is not int or sequence <= 0:
        raise ValueError("Order-group sequence must be a positive integer.")
    if created_at.utcoffset() is None:
        raise ValueError("Order-group creation time must include a timezone.")
    if type(timeout_seconds) is not Decimal or not timeout_seconds.is_finite() or timeout_seconds <= 0:
        raise ValueError("Order-group timeout must be a positive finite Decimal.")

    swap_binding = bindings["swap"]
    treasury_binding = bindings["treasury"]
    swap_quote = quotes["swap"]
    treasury_quote = quotes["treasury"]
    group_id = f"A1:{maturity}:{_target_digest(target_version)}:{sequence:04d}"
    orders = tuple(
        order
        for order in (
            build_leg_order(
                order_ref=f"{group_id}:SWAP",
                maturity=maturity,
                leg="swap",
                delta=reconciliation.swap_delta,
                bid=swap_quote.bid,
                ask=swap_quote.ask,
                min_tick=swap_binding.min_tick,
            ),
            build_leg_order(
                order_ref=f"{group_id}:TREASURY",
                maturity=maturity,
                leg="treasury",
                delta=reconciliation.treasury_delta,
                bid=treasury_quote.bid,
                ask=treasury_quote.ask,
                min_tick=treasury_binding.min_tick,
            ),
        )
        if order is not None
    )
    if not orders:
        return None
    return OrderGroupPlan(
        group_id=group_id,
        maturity=maturity,
        target_version=target_version,
        phase=reconciliation.phase,
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=float(timeout_seconds)),
        start_swap_qty=swap_state.confirmed_qty,
        start_treasury_qty=treasury_state.confirmed_qty,
        requested_swap_delta=reconciliation.swap_delta,
        requested_treasury_delta=reconciliation.treasury_delta,
        orders=orders,
    )


def group_is_timed_out(group: OrderGroupPlan, now: datetime) -> bool:
    if now.utcoffset() is None:
        raise ValueError("Group timeout check requires a timezone-aware time.")
    return now >= group.expires_at


def _progress(filled: int, requested: int) -> Decimal:
    if requested == 0:
        return Decimal("1")
    if filled == 0 or filled * requested < 0:
        return Decimal("0")
    magnitude = min(abs(filled), abs(requested))
    return Decimal(magnitude) / Decimal(abs(requested))


def _quantity_at_progress(requested: int, progress: Decimal) -> int:
    if requested == 0:
        return 0
    with localcontext() as context:
        context.prec = 50
        magnitude = (Decimal(abs(requested)) * progress).to_integral_value(rounding=ROUND_FLOOR)
    return int(magnitude) if requested > 0 else -int(magnitude)


def plan_partial_fill_recovery(
    group: OrderGroupPlan,
    *,
    swap_confirmed_qty: int,
    treasury_confirmed_qty: int,
    residual_within_limit: bool,
) -> PartialFillRecovery:
    swap_filled = swap_confirmed_qty - group.start_swap_qty
    treasury_filled = treasury_confirmed_qty - group.start_treasury_qty
    swap_progress = _progress(swap_filled, group.requested_swap_delta)
    treasury_progress = _progress(treasury_filled, group.requested_treasury_delta)

    if swap_progress >= 1 and treasury_progress >= 1:
        return PartialFillRecovery("complete", 0, 0)

    if not residual_within_limit:
        return PartialFillRecovery(
            "flatten",
            -swap_filled,
            -treasury_filled,
        )

    if swap_progress == treasury_progress:
        return PartialFillRecovery("wait", 0, 0)

    if swap_progress > treasury_progress:
        desired_treasury_filled = _quantity_at_progress(
            group.requested_treasury_delta,
            swap_progress,
        )
        delta = desired_treasury_filled - treasury_filled
        return PartialFillRecovery("hedge" if delta else "wait", 0, delta)

    desired_swap_filled = _quantity_at_progress(
        group.requested_swap_delta,
        treasury_progress,
    )
    delta = desired_swap_filled - swap_filled
    return PartialFillRecovery("hedge" if delta else "wait", delta, 0)


def group_to_state(group: OrderGroupPlan) -> dict[str, object]:
    """Serialize one active group into JSON-safe recovery state."""
    if type(group) is not OrderGroupPlan:
        raise ValueError("Expected an OrderGroupPlan.")
    return {
        "group_id": group.group_id,
        "maturity": group.maturity,
        "target_version": group.target_version,
        "phase": group.phase,
        "created_at": group.created_at.isoformat(),
        "expires_at": group.expires_at.isoformat(),
        "start_swap_qty": group.start_swap_qty,
        "start_treasury_qty": group.start_treasury_qty,
        "requested_swap_delta": group.requested_swap_delta,
        "requested_treasury_delta": group.requested_treasury_delta,
        "orders": [
            {
                "order_ref": order.order_ref,
                "maturity": order.maturity,
                "leg": order.leg,
                "side": order.side,
                "quantity": order.quantity,
                "limit_price": str(order.limit_price),
                "order_type": order.order_type,
                "time_in_force": order.time_in_force,
            }
            for order in group.orders
        ],
    }


def _state_datetime(value: object, field_name: str) -> datetime:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be an ISO datetime.")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone.")
    return parsed


def _state_int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an integer.")
    return value


def group_from_state(raw: dict[str, object]) -> OrderGroupPlan:
    """Restore an active order group from private recovery state."""
    if type(raw) is not dict:
        raise ValueError("Order-group state must be an object.")
    try:
        orders_raw = raw["orders"]
        if type(orders_raw) is not list:
            raise ValueError("orders must be a list")
        orders = []
        for item in orders_raw:
            if type(item) is not dict:
                raise ValueError("order state must be an object")
            quantity = _state_int(item["quantity"], "quantity")
            if quantity <= 0:
                raise ValueError("quantity must be positive")
            price = Decimal(str(item["limit_price"]))
            if not price.is_finite() or price <= 0:
                raise ValueError("limit_price must be positive and finite")
            order = LimitOrderPlan(
                order_ref=str(item["order_ref"]),
                maturity=str(item["maturity"]),
                leg=str(item["leg"]),
                side=str(item["side"]),
                quantity=quantity,
                limit_price=price,
                order_type=str(item["order_type"]),
                time_in_force=str(item["time_in_force"]),
            )
            if not order.order_ref.startswith("A1:") or order.side not in {"BUY", "SELL"}:
                raise ValueError("invalid order identity")
            if order.order_type != "LMT" or order.time_in_force != "DAY":
                raise ValueError("recovered orders must remain DAY limit orders")
            orders.append(order)
        group = OrderGroupPlan(
            group_id=str(raw["group_id"]),
            maturity=str(raw["maturity"]),
            target_version=str(raw["target_version"]),
            phase=str(raw["phase"]),
            created_at=_state_datetime(raw["created_at"], "created_at"),
            expires_at=_state_datetime(raw["expires_at"], "expires_at"),
            start_swap_qty=_state_int(raw["start_swap_qty"], "start_swap_qty"),
            start_treasury_qty=_state_int(raw["start_treasury_qty"], "start_treasury_qty"),
            requested_swap_delta=_state_int(raw["requested_swap_delta"], "requested_swap_delta"),
            requested_treasury_delta=_state_int(raw["requested_treasury_delta"], "requested_treasury_delta"),
            orders=tuple(orders),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError("Invalid Agent 1 order-group recovery state.") from exc
    if not group.group_id.startswith("A1:") or group.maturity not in {"2Y", "5Y"}:
        raise ValueError("Invalid Agent 1 order-group identity.")
    if group.expires_at < group.created_at:
        raise ValueError("Order-group expiry cannot precede creation.")
    return group
