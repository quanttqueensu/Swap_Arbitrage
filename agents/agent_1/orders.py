from __future__ import annotations

from datetime import datetime, timedelta
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from typing import Any, Callable, Literal

from .models import BoundContract, MaturityReconciliation, PositionState, QuoteSnapshot


OrderSide = Literal["BUY", "SELL"]
LegKind = Literal["swap", "treasury"]


class OrderPlanningError(RuntimeError):
    """Raised when a safe limit-order plan cannot be constructed."""


@dataclass(frozen=True)
class LimitOrderPlan:
    order_ref: str
    maturity: str
    leg: LegKind
    side: OrderSide
    quantity: int
    limit_price: Decimal
    order_type: str = "LMT"
    time_in_force: str = "DAY"


def _positive_decimal(value: object, field: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise OrderPlanningError(f"{field} must be a positive finite Decimal.")
    return value


def _align_limit(value: Decimal, tick: Decimal, *, side: OrderSide) -> Decimal:
    rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    with localcontext() as context:
        context.prec = 50
        units = (value / tick).to_integral_value(rounding=rounding)
        return units * tick


def build_leg_order(
    *,
    order_ref: str,
    maturity: str,
    leg: LegKind,
    delta: int,
    bid: Decimal,
    ask: Decimal,
    min_tick: Decimal,
) -> LimitOrderPlan | None:
    if type(delta) is not int:
        raise OrderPlanningError("delta must be an integer contract quantity.")
    if delta == 0:
        return None
    if not isinstance(order_ref, str) or not order_ref.strip():
        raise OrderPlanningError("order_ref must be non-empty.")
    if maturity not in {"2Y", "5Y"}:
        raise OrderPlanningError("maturity must be 2Y or 5Y.")
    if leg not in {"swap", "treasury"}:
        raise OrderPlanningError("leg must be swap or treasury.")

    bid_value = _positive_decimal(bid, "bid")
    ask_value = _positive_decimal(ask, "ask")
    tick = _positive_decimal(min_tick, "min_tick")
    if bid_value > ask_value:
        raise OrderPlanningError("crossed bid/ask quote.")

    side: OrderSide = "BUY" if delta > 0 else "SELL"
    reference = ask_value if side == "BUY" else bid_value
    limit = _align_limit(reference, tick, side=side)

    return LimitOrderPlan(
        order_ref=order_ref.strip(),
        maturity=maturity,
        leg=leg,
        side=side,
        quantity=abs(delta),
        limit_price=limit,
    )


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
    """Serialize only the fields required for restart lifecycle recovery."""
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
    """Restore the compact lifecycle view of an active group.

    Older state files may still contain an ``orders`` array. It is intentionally
    ignored: broker working orders are authoritative after restart, and active
    group lifecycle only needs the group-level quantities and deadline.
    """
    if type(raw) is not dict:
        raise ValueError("Order-group state must be an object.")
    try:
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
            orders=(),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError("Invalid Agent 1 order-group recovery state.") from exc
    if not group.group_id.startswith("A1:") or group.maturity not in {"2Y", "5Y"}:
        raise ValueError("Invalid Agent 1 order-group identity.")
    if group.phase not in {"hold", "reduce", "expand"}:
        raise ValueError("Invalid Agent 1 order-group phase.")
    if group.expires_at < group.created_at:
        raise ValueError("Order-group expiry cannot precede creation.")
    return group


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
