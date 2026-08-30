from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .broker import collect_broker_snapshot, preview_margin
from .contracts import resolve_strategy_bindings
from .models import BoundContract, BrokerSnapshot, DailyTarget
from .orders import build_ib_limit_order
from .planner import CyclePlan, plan_cycle
from .risk import (
    ContractRisk,
    calculate_portfolio_dv01,
    collect_session_pnl,
    load_contract_risks,
    update_drawdown,
)
from .state import AgentState, roll_session
from .targets import DailyCsvTargetProvider, TargetProvider, TargetValidationError


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


def market_is_open(config: object, now: datetime) -> bool:
    if now.utcoffset() is None:
        raise ValueError("Market-hours checks require a timezone-aware time.")
    zone = ZoneInfo(str(getattr(config, "timezone")))
    local = now.astimezone(zone)
    if local.weekday() >= 5:
        return False
    open_time = getattr(config, "market_open_time")
    close_time = getattr(config, "market_close_time")
    return open_time <= local.time().replace(tzinfo=None) < close_time


@dataclass
class RuntimeCache:
    """Per-process cache for resources that are stable within a target day."""

    binding_date: date | None = None
    bindings: dict[str, BoundContract] = field(default_factory=dict)
    risk_key: tuple[object, ...] | None = None
    risks: dict[int, ContractRisk] = field(default_factory=dict)

    def remember_bindings(
        self,
        as_of: date,
        bindings: dict[str, BoundContract],
    ) -> dict[str, BoundContract]:
        self.binding_date = as_of
        self.bindings = dict(bindings)
        return bindings


@dataclass(frozen=True)
class StatusCycleResult:
    target: DailyTarget | None
    target_error: str | None
    bindings: dict[str, BoundContract]
    snapshot: BrokerSnapshot
    risks: dict[int, ContractRisk]
    recovery: RecoveryCheck
    margin_reserve_ok: bool
    session_pnl_usd: Decimal
    session_peak_pnl_usd: Decimal
    drawdown_usd: Decimal
    session_pnl_date: str
    plan: CyclePlan


def preview_groups(
    *,
    ib: Any,
    account_id: str,
    groups: tuple[object, ...],
    bindings: dict[str, BoundContract],
    reserve_fraction: object,
    order_factory: Callable[..., Any] | None,
) -> bool:
    for group in groups:
        maturity = getattr(group, "maturity")
        for plan in getattr(group, "orders"):
            binding = bindings[f"{maturity}:{plan.leg}"]
            if binding.broker_contract is None:
                return False
            order = build_ib_limit_order(
                account_id,
                plan,
                order_factory=order_factory,
            )
            if not preview_margin(
                ib,
                contract=binding.broker_contract,
                order=order,
                reserve_fraction=reserve_fraction,
            ):
                return False
    return True


def _provider_bindings(provider: object) -> dict[str, BoundContract] | None:
    getter = getattr(provider, "current_bindings", None)
    if not callable(getter):
        return None
    try:
        candidate = getter()
    except Exception:
        return None
    expected = {"2Y:swap", "2Y:treasury", "5Y:swap", "5Y:treasury"}
    if not isinstance(candidate, dict) or set(candidate) != expected:
        return None
    if any(not isinstance(binding, BoundContract) for binding in candidate.values()):
        return None
    return dict(candidate)


def _resolve_bindings(
    *,
    ib: Any,
    as_of: date,
    min_days_to_expiry: int,
    held_contracts: dict[str, int],
    provider: object,
    resolver: Callable[..., dict[str, BoundContract]],
    cache: RuntimeCache | None,
) -> dict[str, BoundContract]:
    refreshed = _provider_bindings(provider)
    if refreshed is not None:
        return cache.remember_bindings(as_of, refreshed) if cache is not None else refreshed

    if cache is not None and cache.binding_date == as_of and cache.bindings:
        return dict(cache.bindings)

    bindings = resolver(
        ib,
        as_of=as_of,
        min_days_to_expiry=min_days_to_expiry,
        held_contracts=held_contracts,
    )
    return cache.remember_bindings(as_of, bindings) if cache is not None else bindings


def _risk_cache_key(
    path: Path,
    *,
    as_of: date,
    bindings: dict[str, BoundContract],
) -> tuple[object, ...]:
    try:
        modified = path.stat().st_mtime_ns
    except OSError:
        modified = None
    binding_key = tuple(
        sorted((key, binding.con_id, binding.risk_id) for key, binding in bindings.items())
    )
    return (str(path.resolve()), modified, as_of.isoformat(), binding_key)


def _load_risks(
    path: Path,
    *,
    as_of: date,
    bindings: dict[str, BoundContract],
    cache: RuntimeCache | None,
) -> dict[int, ContractRisk]:
    key = _risk_cache_key(path, as_of=as_of, bindings=bindings)
    if cache is not None and cache.risk_key == key and cache.risks:
        return dict(cache.risks)
    risks = load_contract_risks(path, as_of=as_of, bindings=bindings)
    if cache is not None:
        cache.risk_key = key
        cache.risks = dict(risks)
    return risks


def status_cycle(
    *,
    ib: Any,
    config: object,
    target_path: Path,
    contract_risk_path: Path,
    state: AgentState,
    now: datetime,
    evaluator: Callable[..., Any],
    binding_resolver: Callable[..., dict[str, BoundContract]] = resolve_strategy_bindings,
    snapshot_collector: Callable[..., BrokerSnapshot] = collect_broker_snapshot,
    order_factory: Callable[..., Any] | None = None,
    min_days_to_expiry: int = 14,
    stop_requested: bool = False,
    pnl_collector: Callable[[Any, str], Decimal] = collect_session_pnl,
    target_provider: TargetProvider | None = None,
    runtime_cache: RuntimeCache | None = None,
) -> StatusCycleResult:
    timezone_name = str(getattr(config, "timezone", "America/New_York"))
    session_date = now.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    session_state = roll_session(state, session_date)

    target: DailyTarget | None
    target_error: str | None
    provider = target_provider or DailyCsvTargetProvider(
        target_path,
        getattr(config, "max_target_age_business_days"),
    )
    try:
        target = provider.load_target(now)
        target_error = None
    except TargetValidationError as exc:
        target = None
        target_error = str(exc)

    as_of = target.as_of if target is not None else now.date()
    bindings = _resolve_bindings(
        ib=ib,
        as_of=as_of,
        min_days_to_expiry=min_days_to_expiry,
        held_contracts=session_state.bound_contracts,
        provider=provider,
        resolver=binding_resolver,
        cache=runtime_cache,
    )
    snapshot = snapshot_collector(
        ib,
        account_id=getattr(config, "account"),
        client_id=getattr(config, "client_id"),
        bindings=bindings,
        observed_at=now,
    )
    recovery = reconcile_recovery_state(session_state, snapshot, bindings)
    risks = _load_risks(
        contract_risk_path,
        as_of=as_of,
        bindings=bindings,
        cache=runtime_cache,
    )
    session_pnl_usd = pnl_collector(ib, getattr(config, "account"))
    drawdown_state = update_drawdown(session_state.session_peak_pnl_usd, session_pnl_usd)

    # An existing order group owns the maturity until lifecycle resolution.
    # Normal target planning would be discarded by service.once_cycle anyway,
    # so avoid duplicate reconciliation, risk evaluation and margin previews.
    if session_state.active_groups and not stop_requested:
        pending_plan = CyclePlan(
            action="hold",
            reason_codes=("active_group_pending",),
            groups=(),
            projected_dv01=calculate_portfolio_dv01(snapshot.positions, risks),
            reconciliations={},
            risk_decision=None,
        )
        return StatusCycleResult(
            target=target,
            target_error=target_error,
            bindings=bindings,
            snapshot=snapshot,
            risks=risks,
            recovery=recovery,
            margin_reserve_ok=True,
            session_pnl_usd=session_pnl_usd,
            session_peak_pnl_usd=drawdown_state.peak_pnl_usd,
            drawdown_usd=drawdown_state.drawdown_usd,
            session_pnl_date=session_date,
            plan=pending_plan,
        )

    plan_kwargs = dict(
        target=target,
        target_error=target_error,
        snapshot=snapshot,
        bindings=bindings,
        risks=risks,
        config=config,
        now=now,
        session_order_groups=session_state.session_order_groups,
        group_sequence=session_state.next_group_sequence,
        evaluator=evaluator,
        reconciled=recovery.reconciled,
        stop_requested=stop_requested,
        session_pnl_usd=session_pnl_usd,
        drawdown_usd=drawdown_state.drawdown_usd,
    )
    preliminary = plan_cycle(
        **plan_kwargs,
        margin_reserve_ok=True,
    )
    margin_ok = preview_groups(
        ib=ib,
        account_id=getattr(config, "account"),
        groups=preliminary.groups,
        bindings=bindings,
        reserve_fraction=getattr(config, "margin_reserve_fraction"),
        order_factory=order_factory,
    )
    # Margin normally passes. Reuse the already-computed plan rather than
    # repeating reconciliation, DV01 projection, quote assessment and group
    # construction. A second risk pass is only needed on the failure path.
    final_plan = preliminary if margin_ok else plan_cycle(
        **plan_kwargs,
        margin_reserve_ok=False,
    )

    return StatusCycleResult(
        target=target,
        target_error=target_error,
        bindings=bindings,
        snapshot=snapshot,
        risks=risks,
        recovery=recovery,
        margin_reserve_ok=margin_ok,
        session_pnl_usd=session_pnl_usd,
        session_peak_pnl_usd=drawdown_state.peak_pnl_usd,
        drawdown_usd=drawdown_state.drawdown_usd,
        session_pnl_date=session_date,
        plan=final_plan,
    )
