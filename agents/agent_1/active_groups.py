from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .contract_risk import ContractRisk
from .group_lifecycle import ActiveGroupDecision, evaluate_active_group
from .models import BoundContract, BrokerSnapshot, DailyTarget, MaturityTarget
from .order_groups import group_from_state
from .state import AgentState


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
