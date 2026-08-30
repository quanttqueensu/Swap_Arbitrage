from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from typing import Literal


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
