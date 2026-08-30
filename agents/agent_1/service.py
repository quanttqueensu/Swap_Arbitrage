from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from typing import Any, Callable

from .active_groups import ActiveGroupResolution, resolve_active_groups
from .broker_scope import cancel_group_orders
from .execution import ExecutionResult, execute_cycle_plan
from .market_hours import market_is_open
from .runtime import StatusCycleResult, preview_groups, status_cycle
from .state import AgentState, save_state, load_state
from .supervisor import CyclePlan, plan_cycle


@dataclass(frozen=True)
class OnceCycleResult:
    status: StatusCycleResult
    execution: ExecutionResult
    active_resolution: ActiveGroupResolution | None = None


def _no_execution(state: AgentState, cancelled: tuple[int, ...] = ()) -> ExecutionResult:
    return ExecutionResult(
        submitted_order_ids=dict(state.submitted_order_ids),
        cancelled_order_ids=cancelled,
        state=state,
    )




def _state_after_status(state: AgentState, status: object) -> AgentState:
    target = getattr(status, "target", None)
    bindings = getattr(status, "bindings", {})
    snapshot = getattr(status, "snapshot", None)

    bound_contracts = dict(state.bound_contracts)
    if isinstance(bindings, dict) and bindings:
        candidate = {}
        for key, binding in bindings.items():
            con_id = getattr(binding, "con_id", None)
            if type(key) is str and type(con_id) is int and con_id > 0:
                candidate[key] = con_id
        if candidate:
            bound_contracts = candidate

    broker_snapshot = dict(state.last_successful_broker_snapshot)
    observed_at = getattr(snapshot, "observed_at", None)
    positions = getattr(snapshot, "positions", None)
    working_orders = getattr(snapshot, "working_orders", None)
    if (
        isinstance(observed_at, datetime)
        and observed_at.utcoffset() is not None
        and isinstance(positions, dict)
        and working_orders is not None
    ):
        broker_snapshot = {
            "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
            "positions": {str(key): value for key, value in sorted(positions.items())},
            "working_orders": [
                str(getattr(order, "order_ref", "")) for order in working_orders
            ],
        }

    target_version = getattr(target, "version", state.target_version) if target is not None else state.target_version
    session_pnl_date = getattr(status, "session_pnl_date", state.session_pnl_date)
    session_peak_pnl_usd = getattr(status, "session_peak_pnl_usd", state.session_peak_pnl_usd)
    return replace(
        state,
        target_version=target_version,
        bound_contracts=bound_contracts,
        last_successful_broker_snapshot=broker_snapshot,
        session_pnl_date=session_pnl_date,
        session_peak_pnl_usd=session_peak_pnl_usd,
    )


def once_cycle(
    *,
    ib: Any,
    config: object,
    target_path: Path,
    contract_risk_path: Path,
    state_path: Path,
    now: datetime,
    evaluator: Callable[..., Any],
    stop_requested: bool = False,
    decision_log_path: Path | None = None,
    store: Any | None = None,
    order_factory: Callable[..., Any] | None = None,
    status_runner: Callable[..., StatusCycleResult] = status_cycle,
    executor: Callable[..., ExecutionResult] = execute_cycle_plan,
    planner: Callable[..., CyclePlan] = plan_cycle,
    margin_previewer: Callable[..., bool] = preview_groups,
    active_resolver: Callable[..., ActiveGroupResolution] = resolve_active_groups,
    group_canceller: Callable[..., tuple[int, ...]] = cancel_group_orders,
) -> OnceCycleResult:
    state = load_state(state_path)
    status = status_runner(
        ib=ib,
        config=config,
        target_path=target_path,
        contract_risk_path=contract_risk_path,
        state=state,
        now=now,
        evaluator=evaluator,
        order_factory=order_factory,
        min_days_to_expiry=getattr(config, "min_days_to_expiry"),
        stop_requested=stop_requested,
    )
    refreshed_state = _state_after_status(state, status)
    if refreshed_state != state:
        save_state(state_path, refreshed_state)
    state = refreshed_state

    # Operator stop always wins. A flatten group that Agent 1 already submitted
    # is allowed to work until its own lifecycle says wait/complete/cancel;
    # otherwise the stop path cancels pre-existing strategy orders immediately.
    if stop_requested:
        flatten_active = any(
            isinstance(raw, dict)
            and str(raw.get("target_version", "")).startswith("flatten:")
            for raw in state.active_groups.values()
        )
        if flatten_active:
            resolution = active_resolver(
                state,
                snapshot=status.snapshot,
                bindings=status.bindings,
                risks=status.risks,
                max_residual_fraction=getattr(config, "max_residual_dv01_fraction"),
                now=now,
            )
            if resolution.wait_group_ids:
                return OnceCycleResult(
                    status=status, execution=_no_execution(state),
                    active_resolution=resolution,
                )
            if resolution.cancel_group_ids:
                cancelled: list[int] = []
                for group_id in resolution.cancel_group_ids:
                    cancelled.extend(group_canceller(
                        ib, group_id=group_id,
                        account_id=getattr(config, "account"),
                        client_id=getattr(config, "client_id"),
                    ))
                return OnceCycleResult(
                    status=status, execution=_no_execution(state, tuple(cancelled)),
                    active_resolution=resolution,
                )
            if resolution.completed_group_ids:
                completed = set(resolution.completed_group_ids)
                state = replace(
                    state,
                    active_groups={
                        key: value for key, value in state.active_groups.items()
                        if key not in completed
                    },
                )
                save_state(state_path, state)

        supersede = tuple(state.active_groups) if getattr(status.plan, "groups", ()) else ()
        execution = executor(
            ib=ib,
            config=config,
            plan=status.plan,
            target=status.target,
            snapshot=status.snapshot,
            bindings=status.bindings,
            state=state,
            state_path=state_path,
            decision_log_path=decision_log_path,
            now=now,
            store=store,
            order_factory=order_factory,
            supersede_group_ids=supersede,
        )
        return OnceCycleResult(status=status, execution=execution)

    active_resolution: ActiveGroupResolution | None = None
    state_for_action = state
    if state.active_groups:
        active_resolution = active_resolver(
            state,
            snapshot=status.snapshot,
            bindings=status.bindings,
            risks=status.risks,
            max_residual_fraction=getattr(config, "max_residual_dv01_fraction"),
            now=now,
        )
        if active_resolution.completed_group_ids:
            completed = set(active_resolution.completed_group_ids)
            state_for_action = replace(
                state_for_action,
                active_groups={
                    key: value for key, value in state_for_action.active_groups.items()
                    if key not in completed
                },
            )

        if active_resolution.cancel_group_ids:
            cancelled: list[int] = []
            for group_id in active_resolution.cancel_group_ids:
                cancelled.extend(group_canceller(
                    ib,
                    group_id=group_id,
                    account_id=getattr(config, "account"),
                    client_id=getattr(config, "client_id"),
                ))
            if state_for_action != state:
                save_state(state_path, state_for_action)
            return OnceCycleResult(
                status=status,
                execution=_no_execution(state_for_action, tuple(cancelled)),
                active_resolution=active_resolution,
            )

        if active_resolution.wait_group_ids:
            if state_for_action != state:
                save_state(state_path, state_for_action)
            return OnceCycleResult(
                status=status,
                execution=_no_execution(state_for_action),
                active_resolution=active_resolution,
            )

        if active_resolution.recovery_target is not None:
            recovery_target = active_resolution.recovery_target
            preliminary = planner(
                target=recovery_target,
                target_error=None,
                snapshot=status.snapshot,
                bindings=status.bindings,
                risks=status.risks,
                config=config,
                now=now,
                session_order_groups=state_for_action.session_order_groups,
                evaluator=evaluator,
                margin_reserve_ok=True,
                reconciled=status.recovery.reconciled,
            )
            margin_ok = margin_previewer(
                ib=ib,
                account_id=getattr(config, "account"),
                groups=preliminary.groups,
                bindings=status.bindings,
                reserve_fraction=getattr(config, "margin_reserve_fraction"),
                order_factory=order_factory,
            )
            recovery_plan = planner(
                target=recovery_target,
                target_error=None,
                snapshot=status.snapshot,
                bindings=status.bindings,
                risks=status.risks,
                config=config,
                now=now,
                session_order_groups=state_for_action.session_order_groups,
                evaluator=evaluator,
                margin_reserve_ok=margin_ok,
                reconciled=status.recovery.reconciled,
            )
            execution = executor(
                ib=ib,
                config=config,
                plan=recovery_plan,
                target=recovery_target,
                snapshot=status.snapshot,
                bindings=status.bindings,
                state=state_for_action,
                state_path=state_path,
                decision_log_path=decision_log_path,
                now=now,
                store=store,
                order_factory=order_factory,
                supersede_group_ids=active_resolution.supersede_group_ids,
            )
            return OnceCycleResult(
                status=status,
                execution=execution,
                active_resolution=active_resolution,
            )

        if active_resolution.completed_group_ids:
            # Do not immediately use the same broker snapshot for a new strategy
            # group after lifecycle completion; the next poll starts fresh.
            save_state(state_path, state_for_action)
            return OnceCycleResult(
                status=status,
                execution=_no_execution(state_for_action),
                active_resolution=active_resolution,
            )

    execution = executor(
        ib=ib,
        config=config,
        plan=status.plan,
        target=status.target,
        snapshot=status.snapshot,
        bindings=status.bindings,
        state=state_for_action,
        state_path=state_path,
        decision_log_path=decision_log_path,
        now=now,
        store=store,
        order_factory=order_factory,
        supersede_group_ids=(),
    )
    return OnceCycleResult(
        status=status,
        execution=execution,
        active_resolution=active_resolution,
    )


def polling_loop(
    *,
    config: object,
    cycle: Callable[[datetime], Any],
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep_fn: Callable[[float], Any] = sleep,
    max_iterations: int | None = None,
) -> None:
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        now = now_fn()
        if market_is_open(config, now):
            cycle(now)
        sleep_fn(getattr(config, "poll_interval_seconds"))
        iterations += 1
