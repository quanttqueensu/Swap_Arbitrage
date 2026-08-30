from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from .contract_risk import ContractRisk, calculate_portfolio_dv01
from .models import BoundContract, BrokerSnapshot
from .order_groups import OrderGroupPlan, group_is_timed_out, plan_partial_fill_recovery


LifecycleAction = Literal[
    "wait", "complete", "cancel_partial", "cancel_timeout", "hedge", "flatten"
]


@dataclass(frozen=True)
class ActiveGroupDecision:
    group_id: str
    maturity: str
    action: LifecycleAction
    swap_delta: int = 0
    treasury_delta: int = 0


def _working_refs(snapshot: BrokerSnapshot, group: OrderGroupPlan) -> tuple[str, ...]:
    prefix = f"{group.group_id}:"
    return tuple(
        order.order_ref
        for order in snapshot.working_orders
        if order.order_ref.startswith(prefix)
    )


def evaluate_active_group(
    group: OrderGroupPlan,
    *,
    snapshot: BrokerSnapshot,
    bindings: dict[str, BoundContract],
    risks: dict[int, ContractRisk],
    max_residual_fraction: Decimal,
    now: datetime,
) -> ActiveGroupDecision:
    if now.utcoffset() is None:
        raise ValueError("Active-group evaluation requires a timezone-aware time.")
    if (
        type(max_residual_fraction) is not Decimal
        or not max_residual_fraction.is_finite()
        or max_residual_fraction < 0
    ):
        raise ValueError("Maximum residual-DV01 fraction is invalid.")

    swap_binding = bindings[f"{group.maturity}:swap"]
    treasury_binding = bindings[f"{group.maturity}:treasury"]
    working = _working_refs(snapshot, group)

    if working and group_is_timed_out(group, now):
        return ActiveGroupDecision(group.group_id, group.maturity, "cancel_timeout")

    exposure = calculate_portfolio_dv01(snapshot.positions, risks)
    recovery = plan_partial_fill_recovery(
        group,
        swap_confirmed_qty=snapshot.positions.get(swap_binding.con_id, 0),
        treasury_confirmed_qty=snapshot.positions.get(treasury_binding.con_id, 0),
        residual_within_limit=(
            True if group.phase == "reduce"
            else exposure.residual_fraction <= max_residual_fraction
        ),
    )

    if recovery.action == "complete":
        return ActiveGroupDecision(group.group_id, group.maturity, "complete")
    if recovery.action in {"hedge", "flatten"} and working:
        return ActiveGroupDecision(group.group_id, group.maturity, "cancel_partial")
    if recovery.action == "hedge":
        return ActiveGroupDecision(
            group.group_id, group.maturity, "hedge",
            recovery.swap_delta, recovery.treasury_delta,
        )
    if recovery.action == "flatten":
        return ActiveGroupDecision(
            group.group_id, group.maturity, "flatten",
            recovery.swap_delta, recovery.treasury_delta,
        )
    return ActiveGroupDecision(group.group_id, group.maturity, "wait")
