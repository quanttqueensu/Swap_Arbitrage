from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from typing import Any, Callable

from .broker import cancel_group_orders
from .cycle import (
    RuntimeCache,
    StatusCycleResult,
    market_is_open,
    preview_groups,
    status_cycle,
)
from .execution import ExecutionResult, execute_cycle_plan
from .lifecycle import ActiveGroupResolution, resolve_active_groups
from .planner import CyclePlan, plan_cycle
from .state import AgentState, load_state, roll_session, save_state


@dataclass(frozen=True)
class OnceCycleResult:
    status: StatusCycleResult
    execution: ExecutionResult
    active_resolution: ActiveGroupResolution | None = None


@dataclass(frozen=True)
class CycleHooks:
    """Replaceable side-effect seams used by deterministic tests."""

    status_runner: Callable[..., StatusCycleResult] = status_cycle
    executor: Callable[..., ExecutionResult] = execute_cycle_plan
    planner: Callable[..., CyclePlan] = plan_cycle
    margin_previewer: Callable[..., bool] = preview_groups
    active_resolver: Callable[..., ActiveGroupResolution] = resolve_active_groups
    group_canceller: Callable[..., tuple[int, ...]] = cancel_group_orders


@dataclass
class Agent1Engine:
    """Configured Agent 1 supervisor with stable dependencies bound once."""

    ib: Any
    config: object
    target_path: Path
    contract_risk_path: Path
    state_path: Path
    evaluator: Callable[..., Any]
    decision_log_path: Path | None = None
    store: Any | None = None
    order_factory: Callable[..., Any] | None = None
    target_provider: object | None = None
    runtime_cache: RuntimeCache = field(default_factory=RuntimeCache)
    hooks: CycleHooks = field(default_factory=CycleHooks)

    def cycle(self, now: datetime, *, stop_requested: bool = False) -> OnceCycleResult:
        state = load_state(self.state_path)
        status = self._status(state, now, stop_requested=stop_requested)
        state = self._refresh_state(state, status)

        if stop_requested:
            return self._handle_stop(state, status, now)

        if state.active_groups:
            handled = self._handle_active_groups(state, status, now)
            if handled is not None:
                return handled

        execution = self._execute(
            status=status,
            state=state,
            plan=status.plan,
            target=status.target,
            now=now,
        )
        return OnceCycleResult(status=status, execution=execution)

    def _status(
        self,
        state: AgentState,
        now: datetime,
        *,
        stop_requested: bool,
    ) -> StatusCycleResult:
        return self.hooks.status_runner(
            ib=self.ib,
            config=self.config,
            target_path=self.target_path,
            contract_risk_path=self.contract_risk_path,
            state=state,
            now=now,
            evaluator=self.evaluator,
            order_factory=self.order_factory,
            min_days_to_expiry=getattr(self.config, "min_days_to_expiry"),
            stop_requested=stop_requested,
            target_provider=self.target_provider,
            runtime_cache=self.runtime_cache,
        )

    def _refresh_state(self, state: AgentState, status: StatusCycleResult) -> AgentState:
        refreshed = _state_after_status(state, status)
        if refreshed != state:
            save_state(self.state_path, refreshed)
        return refreshed

    def _resolve_active(
        self,
        state: AgentState,
        status: StatusCycleResult,
        now: datetime,
    ) -> ActiveGroupResolution:
        return self.hooks.active_resolver(
            state,
            snapshot=status.snapshot,
            bindings=status.bindings,
            risks=status.risks,
            max_residual_fraction=getattr(self.config, "max_residual_dv01_fraction"),
            now=now,
        )

    def _cancel_groups(self, group_ids: tuple[str, ...]) -> tuple[int, ...]:
        cancelled: list[int] = []
        for group_id in group_ids:
            cancelled.extend(
                self.hooks.group_canceller(
                    self.ib,
                    group_id=group_id,
                    account_id=getattr(self.config, "account"),
                    client_id=getattr(self.config, "client_id"),
                )
            )
        return tuple(cancelled)

    def _execute(
        self,
        *,
        status: StatusCycleResult,
        state: AgentState,
        plan: CyclePlan,
        target: object,
        now: datetime,
        supersede_group_ids: tuple[str, ...] = (),
    ) -> ExecutionResult:
        return self.hooks.executor(
            ib=self.ib,
            config=self.config,
            plan=plan,
            target=target,
            snapshot=status.snapshot,
            bindings=status.bindings,
            state=state,
            state_path=self.state_path,
            decision_log_path=self.decision_log_path,
            now=now,
            store=self.store,
            order_factory=self.order_factory,
            supersede_group_ids=supersede_group_ids,
        )

    def _plan_recovery(
        self,
        *,
        target: object,
        state: AgentState,
        status: StatusCycleResult,
        now: datetime,
    ) -> CyclePlan:
        plan_kwargs = dict(
            target=target,
            target_error=None,
            snapshot=status.snapshot,
            bindings=status.bindings,
            risks=status.risks,
            config=self.config,
            now=now,
            session_order_groups=state.session_order_groups,
            group_sequence=state.next_group_sequence,
            evaluator=self.evaluator,
            reconciled=status.recovery.reconciled,
        )
        preliminary = self.hooks.planner(**plan_kwargs, margin_reserve_ok=True)
        margin_ok = self.hooks.margin_previewer(
            ib=self.ib,
            account_id=getattr(self.config, "account"),
            groups=preliminary.groups,
            bindings=status.bindings,
            reserve_fraction=getattr(self.config, "margin_reserve_fraction"),
            order_factory=self.order_factory,
        )
        return preliminary if margin_ok else self.hooks.planner(
            **plan_kwargs,
            margin_reserve_ok=False,
        )

    def _handle_stop(
        self,
        state: AgentState,
        status: StatusCycleResult,
        now: datetime,
    ) -> OnceCycleResult:
        # A flatten group already submitted by Agent 1 may work until lifecycle
        # resolution says wait/complete/cancel. All other stop paths use the
        # normal stop plan, which is scoped to Agent 1 orders only.
        flatten_active = any(
            isinstance(raw, dict)
            and str(raw.get("target_version", "")).startswith("flatten:")
            for raw in state.active_groups.values()
        )
        if flatten_active:
            resolution = self._resolve_active(state, status, now)
            if resolution.wait_group_ids:
                return OnceCycleResult(
                    status=status,
                    execution=_no_execution(state),
                    active_resolution=resolution,
                )
            if resolution.cancel_group_ids:
                return OnceCycleResult(
                    status=status,
                    execution=_no_execution(
                        state,
                        self._cancel_groups(resolution.cancel_group_ids),
                    ),
                    active_resolution=resolution,
                )
            if resolution.completed_group_ids:
                state = _without_groups(state, resolution.completed_group_ids)
                save_state(self.state_path, state)

        supersede = tuple(state.active_groups) if getattr(status.plan, "groups", ()) else ()
        execution = self._execute(
            status=status,
            state=state,
            plan=status.plan,
            target=status.target,
            now=now,
            supersede_group_ids=supersede,
        )
        return OnceCycleResult(status=status, execution=execution)

    def _handle_active_groups(
        self,
        state: AgentState,
        status: StatusCycleResult,
        now: datetime,
    ) -> OnceCycleResult | None:
        resolution = self._resolve_active(state, status, now)
        state_for_action = _without_groups(state, resolution.completed_group_ids)

        if resolution.cancel_group_ids:
            self._persist_if_changed(state, state_for_action)
            return OnceCycleResult(
                status=status,
                execution=_no_execution(
                    state_for_action,
                    self._cancel_groups(resolution.cancel_group_ids),
                ),
                active_resolution=resolution,
            )

        if resolution.wait_group_ids:
            self._persist_if_changed(state, state_for_action)
            return OnceCycleResult(
                status=status,
                execution=_no_execution(state_for_action),
                active_resolution=resolution,
            )

        if resolution.recovery_target is not None:
            recovery_plan = self._plan_recovery(
                target=resolution.recovery_target,
                state=state_for_action,
                status=status,
                now=now,
            )
            execution = self._execute(
                status=status,
                state=state_for_action,
                plan=recovery_plan,
                target=resolution.recovery_target,
                now=now,
                supersede_group_ids=resolution.supersede_group_ids,
            )
            return OnceCycleResult(
                status=status,
                execution=execution,
                active_resolution=resolution,
            )

        if resolution.completed_group_ids:
            # A fresh broker snapshot is required before opening a new strategy
            # group after lifecycle completion.
            save_state(self.state_path, state_for_action)
            return OnceCycleResult(
                status=status,
                execution=_no_execution(state_for_action),
                active_resolution=resolution,
            )

        return None

    def _persist_if_changed(self, previous: AgentState, current: AgentState) -> None:
        if current != previous:
            save_state(self.state_path, current)


def _no_execution(state: AgentState, cancelled: tuple[int, ...] = ()) -> ExecutionResult:
    return ExecutionResult(
        submitted_order_ids=dict(state.submitted_order_ids),
        cancelled_order_ids=cancelled,
        state=state,
    )


def _without_groups(state: AgentState, group_ids: tuple[str, ...]) -> AgentState:
    if not group_ids:
        return state
    removed = set(group_ids)
    return replace(
        state,
        active_groups={
            key: value for key, value in state.active_groups.items()
            if key not in removed
        },
    )


def _state_after_status(state: AgentState, status: object) -> AgentState:
    target = getattr(status, "target", None)
    bindings = getattr(status, "bindings", {})
    snapshot = getattr(status, "snapshot", None)

    bound_contracts = dict(state.bound_contracts)
    if isinstance(bindings, dict) and bindings:
        candidate = {
            key: con_id
            for key, binding in bindings.items()
            if type(key) is str
            and type(con_id := getattr(binding, "con_id", None)) is int
            and con_id > 0
        }
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

    target_version = (
        getattr(target, "version", state.target_version)
        if target is not None
        else state.target_version
    )
    session_pnl_date = getattr(status, "session_pnl_date", state.session_pnl_date)
    session_peak_pnl_usd = getattr(
        status,
        "session_peak_pnl_usd",
        state.session_peak_pnl_usd,
    )
    base_state = (
        roll_session(state, session_pnl_date)
        if session_pnl_date and session_pnl_date != state.session_pnl_date
        else state
    )
    return replace(
        base_state,
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
    target_provider: object | None = None,
    runtime_cache: RuntimeCache | None = None,
) -> OnceCycleResult:
    """Compatibility wrapper around the configured engine.

    Production code constructs one Agent1Engine and reuses it. This wrapper is
    retained for existing deterministic tests and external callers that inject
    individual seams.
    """
    engine = Agent1Engine(
        ib=ib,
        config=config,
        target_path=target_path,
        contract_risk_path=contract_risk_path,
        state_path=state_path,
        evaluator=evaluator,
        decision_log_path=decision_log_path,
        store=store,
        order_factory=order_factory,
        target_provider=target_provider,
        runtime_cache=runtime_cache or RuntimeCache(),
        hooks=CycleHooks(
            status_runner=status_runner,
            executor=executor,
            planner=planner,
            margin_previewer=margin_previewer,
            active_resolver=active_resolver,
            group_canceller=group_canceller,
        ),
    )
    return engine.cycle(now, stop_requested=stop_requested)


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
