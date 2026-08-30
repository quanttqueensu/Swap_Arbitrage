from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .broker import cancel_agent1_orders
from .audit import DecisionLogError, build_decision_rows, write_decisions
from .models import BoundContract, BrokerSnapshot, DailyTarget
from .orders import (
    LimitOrderPlan,
    OrderGroupPlan,
    build_ib_limit_order,
    group_to_state,
)
from .state import AgentState, StateError, save_state
from .planner import CyclePlan


class ExecutionError(RuntimeError):
    """Raised when a paper execution cycle cannot be completed safely."""


@dataclass(frozen=True)
class ExecutionResult:
    submitted_order_ids: dict[str, int]
    cancelled_order_ids: tuple[int, ...]
    state: AgentState


_PENDING_STATUSES = {"", "ApiPending", "PendingSubmit", "PendingCancel"}
_REJECTED_STATUSES = {"ApiCancelled", "Cancelled", "Inactive"}


def _snapshot_state(snapshot: BrokerSnapshot) -> dict[str, object]:
    return {
        "observed_at": snapshot.observed_at.astimezone(timezone.utc).isoformat(),
        "positions": {str(key): value for key, value in sorted(snapshot.positions.items())},
        "working_orders": [order.order_ref for order in snapshot.working_orders],
    }


def _planned_state(
    *,
    state: AgentState,
    target: DailyTarget | None,
    snapshot: BrokerSnapshot,
    bindings: dict[str, BoundContract],
    groups: tuple[OrderGroupPlan, ...],
    supersede_group_ids: tuple[str, ...] = (),
) -> AgentState:
    refs = list(state.submitted_order_refs)
    active_groups = {
        key: value for key, value in state.active_groups.items()
        if key not in set(supersede_group_ids)
    }
    for group in groups:
        active_groups[group.group_id] = group_to_state(group)
        for order in group.orders:
            if order.order_ref not in refs:
                refs.append(order.order_ref)
    return AgentState(
        target_version=target.version if target is not None else state.target_version,
        bound_contracts={key: binding.con_id for key, binding in bindings.items()},
        submitted_order_refs=tuple(refs),
        submitted_order_ids=dict(state.submitted_order_ids),
        active_groups=active_groups,
        last_successful_broker_snapshot=_snapshot_state(snapshot),
        session_order_groups=state.session_order_groups + len(groups),
        next_group_sequence=state.next_group_sequence + len(groups),
        session_pnl_date=state.session_pnl_date,
        session_peak_pnl_usd=state.session_peak_pnl_usd,
    )


def _binding_for_order(bindings: dict[str, BoundContract], plan: LimitOrderPlan) -> BoundContract:
    key = f"{plan.maturity}:{plan.leg}"
    try:
        binding = bindings[key]
    except KeyError as exc:
        raise ExecutionError(f"Missing bound contract for {key}.") from exc
    if binding.broker_contract is None:
        raise ExecutionError(f"Bound contract {key} has no broker contract object.")
    return binding


def _trade_status(trade: Any) -> str:
    return str(getattr(getattr(trade, "orderStatus", None), "status", "") or "")


def _trade_has_error(trade: Any) -> bool:
    return any(bool(getattr(entry, "errorCode", 0)) for entry in getattr(trade, "log", ()))


def _confirmed_order_id(trade: Any) -> int:
    order_id = getattr(getattr(trade, "order", None), "orderId", None)
    if type(order_id) is not int or order_id <= 0:
        raise ExecutionError("IBKR did not confirm a positive paper order ID.")
    return order_id


def _submit_one(
    *,
    ib: Any,
    contract: Any,
    order: Any,
    wait_seconds: float,
    wait_attempts: int,
) -> tuple[Any, str, int]:
    trade = ib.placeOrder(contract, order)
    status = _trade_status(trade)
    for _ in range(wait_attempts):
        if status not in _PENDING_STATUSES:
            break
        ib.sleep(wait_seconds)
        status = _trade_status(trade)
    if status in _REJECTED_STATUSES or _trade_has_error(trade):
        raise ExecutionError(f"IBKR rejected Agent 1 paper order {order.orderRef}.")
    if status in _PENDING_STATUSES:
        raise ExecutionError(f"IBKR did not confirm Agent 1 paper order {order.orderRef}.")
    return trade, status, _confirmed_order_id(trade)


def _paper_order_row(
    *,
    group: OrderGroupPlan,
    plan: LimitOrderPlan,
    binding: BoundContract,
    status: str,
    order_id: int,
    now: datetime,
) -> dict[str, object]:
    return {
        "order_ref": plan.order_ref,
        "decision_id": group.group_id,
        "created_at_utc": now,
        "instrument_id": f"IBKR:{binding.con_id}",
        "side": plan.side,
        "quantity": plan.quantity if plan.side == "BUY" else -plan.quantity,
        "order_type": "LMT",
        "time_in_force": "DAY",
        "status": status,
        "ibkr_order_id": str(order_id),
    }


def execute_cycle_plan(
    *,
    ib: Any,
    config: object,
    plan: CyclePlan,
    target: DailyTarget | None,
    snapshot: BrokerSnapshot,
    bindings: dict[str, BoundContract],
    state: AgentState,
    state_path: Path,
    now: datetime,
    decision_log_path: Path | None = None,
    store: Any | None = None,
    order_factory: Callable[[str, int, float], Any] | None = None,
    wait_seconds: float = 0.25,
    wait_attempts: int = 20,
    supersede_group_ids: tuple[str, ...] = (),
) -> ExecutionResult:
    """Execute one already-risk-approved paper cycle.

    Recovery state is persisted before submission. Broker truth remains authoritative
    on the next supervisor cycle.
    """
    if now.utcoffset() is None:
        raise ExecutionError("Execution time must include a timezone.")
    account_id = str(getattr(config, "account", ""))
    client_id = getattr(config, "client_id", None)
    if not account_id.upper().startswith("DU") or type(client_id) is not int or client_id <= 0 or client_id == 30:
        raise ExecutionError("Unsafe Agent 1 paper execution identity.")

    if decision_log_path is not None:
        try:
            write_decisions(
                decision_log_path,
                build_decision_rows(
                    target=target, snapshot=snapshot, bindings=bindings, plan=plan, now=now
                ),
            )
        except DecisionLogError as exc:
            raise ExecutionError("Agent 1 decision audit could not be persisted.") from exc

    if plan.action == "cancel_then_flatten":
        cancelled = cancel_agent1_orders(ib, account_id=account_id, client_id=client_id)
        return ExecutionResult(dict(state.submitted_order_ids), cancelled, state)

    if not plan.groups:
        return ExecutionResult(dict(state.submitted_order_ids), (), state)

    prepared = _planned_state(
        state=state,
        target=target,
        snapshot=snapshot,
        bindings=bindings,
        groups=plan.groups,
        supersede_group_ids=supersede_group_ids,
    )
    try:
        save_state(state_path, prepared)
    except StateError as exc:
        raise ExecutionError("Agent 1 state could not be persisted before submission.") from exc

    current = prepared
    submitted: dict[str, int] = dict(current.submitted_order_ids)
    try:
        for group in plan.groups:
            for order_plan in group.orders:
                binding = _binding_for_order(bindings, order_plan)
                order = build_ib_limit_order(account_id, order_plan, order_factory=order_factory)
                trade, status, order_id = _submit_one(
                    ib=ib,
                    contract=binding.broker_contract,
                    order=order,
                    wait_seconds=wait_seconds,
                    wait_attempts=wait_attempts,
                )
                submitted[order_plan.order_ref] = order_id
                current = replace(current, submitted_order_ids=dict(submitted))
                try:
                    save_state(state_path, current)
                except StateError as exc:
                    cancel_agent1_orders(ib, account_id=account_id, client_id=client_id)
                    raise ExecutionError("Agent 1 state write failed after broker confirmation.") from exc
                if store is not None:
                    store.write(
                        "paper_orders",
                        [_paper_order_row(
                            group=group,
                            plan=order_plan,
                            binding=binding,
                            status=status,
                            order_id=order_id,
                            now=now,
                        )],
                    )
    except ExecutionError:
        cancel_agent1_orders(ib, account_id=account_id, client_id=client_id)
        raise
    except Exception as exc:
        cancel_agent1_orders(ib, account_id=account_id, client_id=client_id)
        raise ExecutionError("Agent 1 paper submission failed closed.") from exc

    return ExecutionResult(submitted, (), current)
