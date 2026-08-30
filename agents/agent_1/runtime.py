from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .account_risk import collect_session_pnl, update_drawdown
from .broker import collect_broker_snapshot
from .contract_risk import ContractRisk, load_contract_risks
from .contracts import resolve_strategy_bindings
from .ib_orders import build_ib_limit_order
from .margin import preview_margin
from .models import BoundContract, BrokerSnapshot, DailyTarget
from .recovery import RecoveryCheck, reconcile_recovery_state
from .state import AgentState
from .supervisor import CyclePlan, plan_cycle
from .target_loader import TargetValidationError
from .target_provider import DailyCsvTargetProvider, TargetProvider


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
) -> StatusCycleResult:
    target: DailyTarget | None
    target_error: str | None
    try:
        provider = target_provider or DailyCsvTargetProvider(
            target_path,
            getattr(config, "max_target_age_business_days"),
        )
        target = provider.load_target(now)
        target_error = None
    except TargetValidationError as exc:
        target = None
        target_error = str(exc)

    as_of = target.as_of if target is not None else now.date()
    bindings = binding_resolver(
        ib,
        as_of=as_of,
        min_days_to_expiry=min_days_to_expiry,
        held_contracts=state.bound_contracts,
    )
    snapshot = snapshot_collector(
        ib,
        account_id=getattr(config, "account"),
        client_id=getattr(config, "client_id"),
        bindings=bindings,
        observed_at=now,
    )
    recovery = reconcile_recovery_state(state, snapshot, bindings)
    risks = load_contract_risks(
        contract_risk_path,
        as_of=as_of,
        bindings=bindings,
    )
    session_pnl_usd = pnl_collector(ib, getattr(config, "account"))
    session_date = now.astimezone(
        ZoneInfo(str(getattr(config, "timezone", "America/New_York")))
    ).date().isoformat()
    previous_peak = (
        state.session_peak_pnl_usd
        if state.session_pnl_date == session_date
        else Decimal("0")
    )
    drawdown_state = update_drawdown(previous_peak, session_pnl_usd)

    preliminary = plan_cycle(
        target=target,
        target_error=target_error,
        snapshot=snapshot,
        bindings=bindings,
        risks=risks,
        config=config,
        now=now,
        session_order_groups=state.session_order_groups,
        evaluator=evaluator,
        margin_reserve_ok=True,
        reconciled=recovery.reconciled,
        stop_requested=stop_requested,
        session_pnl_usd=session_pnl_usd,
        drawdown_usd=drawdown_state.drawdown_usd,
    )
    margin_ok = preview_groups(
        ib=ib,
        account_id=getattr(config, "account"),
        groups=preliminary.groups,
        bindings=bindings,
        reserve_fraction=getattr(config, "margin_reserve_fraction"),
        order_factory=order_factory,
    )
    final_plan = plan_cycle(
        target=target,
        target_error=target_error,
        snapshot=snapshot,
        bindings=bindings,
        risks=risks,
        config=config,
        now=now,
        session_order_groups=state.session_order_groups,
        evaluator=evaluator,
        margin_reserve_ok=margin_ok,
        reconciled=recovery.reconciled,
        stop_requested=stop_requested,
        session_pnl_usd=session_pnl_usd,
        drawdown_usd=drawdown_state.drawdown_usd,
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
