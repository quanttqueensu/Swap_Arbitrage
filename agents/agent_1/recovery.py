from __future__ import annotations

from dataclasses import dataclass

from .models import BoundContract, BrokerSnapshot
from .state import AgentState


@dataclass(frozen=True)
class RecoveryCheck:
    reconciled: bool
    reasons: tuple[str, ...]


def reconcile_recovery_state(
    state: AgentState,
    snapshot: BrokerSnapshot,
    bindings: dict[str, BoundContract],
) -> RecoveryCheck:
    reasons: list[str] = []

    for key, previous_con_id in state.bound_contracts.items():
        current = bindings.get(key)
        if current is not None and current.con_id != previous_con_id:
            if "binding_mismatch" not in reasons:
                reasons.append("binding_mismatch")

    known_refs = set(state.submitted_order_refs)
    for order in snapshot.working_orders:
        if order.order_ref not in known_refs:
            if "unknown_working_order" not in reasons:
                reasons.append("unknown_working_order")

    return RecoveryCheck(reconciled=not reasons, reasons=tuple(reasons))
