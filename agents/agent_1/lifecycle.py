from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from .models import BoundContract, BrokerSnapshot, DailyTarget, MaturityTarget
from .orders import (
    OrderGroupPlan,
    group_from_state,
    group_is_timed_out,
    plan_partial_fill_recovery,
)
from .risk import ContractRisk, calculate_portfolio_dv01
from .state import AgentState


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


@dataclass(frozen=True)
class ActiveGroupResolution:
    decisions: tuple[ActiveGroupDecision, ...]
    completed_group_ids: tuple[str, ...]
    cancel_group_ids: tuple[str, ...]
    wait_group_ids: tuple[str, ...]
    supersede_group_ids: tuple[str, ...]
    recovery_target: DailyTarget | None

    @property
    def blocks_normal_cycle(self) -> bool:
        return bool(
            self.cancel_group_ids
            or self.wait_group_ids
            or self.supersede_group_ids
            or self.recovery_target is not None
        )


def _current_target(
    snapshot: BrokerSnapshot,
    bindings: dict[str, BoundContract],
    maturity: str,
) -> MaturityTarget:
    swap = bindings[f"{maturity}:swap"]
    treasury = bindings[f"{maturity}:treasury"]
    return MaturityTarget(
        snapshot.position_state(swap.con_id).effective_qty,
        snapshot.position_state(treasury.con_id).effective_qty,
    )


def _recovery_version(decisions: list[ActiveGroupDecision]) -> str:
    payload = [
        {
            "group_id": item.group_id,
            "action": item.action,
            "swap_delta": item.swap_delta,
            "treasury_delta": item.treasury_delta,
        }
        for item in decisions
    ]
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"recovery:{digest}"


def resolve_active_groups(
    state: AgentState,
    *,
    snapshot: BrokerSnapshot,
    bindings: dict[str, BoundContract],
    risks: dict[int, ContractRisk],
    max_residual_fraction: Decimal,
    now: datetime,
) -> ActiveGroupResolution:
    decisions: list[ActiveGroupDecision] = []
    for group_id in sorted(state.active_groups):
        group = group_from_state(state.active_groups[group_id])
        decisions.append(
            evaluate_active_group(
                group,
                snapshot=snapshot,
                bindings=bindings,
                risks=risks,
                max_residual_fraction=max_residual_fraction,
                now=now,
            )
        )

    completed = tuple(item.group_id for item in decisions if item.action == "complete")
    cancelled = tuple(
        item.group_id
        for item in decisions
        if item.action in {"cancel_partial", "cancel_timeout"}
    )
    waiting = tuple(item.group_id for item in decisions if item.action == "wait")
    recovery = [item for item in decisions if item.action in {"hedge", "flatten"}]
    supersede = tuple(item.group_id for item in recovery)

    recovery_target: DailyTarget | None = None
    if recovery:
        targets = {
            "2Y": _current_target(snapshot, bindings, "2Y"),
            "5Y": _current_target(snapshot, bindings, "5Y"),
        }
        for item in recovery:
            current = targets[item.maturity]
            targets[item.maturity] = MaturityTarget(
                current.swap_qty + item.swap_delta,
                current.treasury_qty + item.treasury_delta,
            )
        recovery_target = DailyTarget(
            as_of=now.date(),
            version=_recovery_version(recovery),
            age_business_days=0,
            target_2y=targets["2Y"],
            target_5y=targets["5Y"],
        )

    return ActiveGroupResolution(
        decisions=tuple(decisions),
        completed_group_ids=completed,
        cancel_group_ids=cancelled,
        wait_group_ids=waiting,
        supersede_group_ids=supersede,
        recovery_target=recovery_target,
    )
