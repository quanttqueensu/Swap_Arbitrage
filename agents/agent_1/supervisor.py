from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Literal

from .broker import assess_quotes
from .contract_risk import ContractRisk, PortfolioDV01, calculate_portfolio_dv01
from .models import (
    BoundContract,
    BrokerSnapshot,
    DailyTarget,
    MaturityReconciliation,
    MaturityTarget,
)
from .order_groups import OrderGroupPlan, build_order_group
from .reconciliation import reconcile_maturity
from .risk_adapter import RuntimeRiskState, evaluate_runtime_risk


CycleAction = Literal[
    "hold",
    "trade",
    "blocked",
    "flatten",
    "cancel_then_flatten",
    "flatten_wait",
]


@dataclass(frozen=True)
class CyclePlan:
    action: CycleAction
    reason_codes: tuple[str, ...]
    groups: tuple[OrderGroupPlan, ...]
    projected_dv01: PortfolioDV01
    reconciliations: dict[str, MaturityReconciliation]
    risk_decision: Any | None = None


def _zero_target() -> MaturityTarget:
    return MaturityTarget(0, 0)


def _has_open_position(snapshot: BrokerSnapshot) -> bool:
    return any(quantity != 0 for quantity in snapshot.positions.values())


def _active_group_count(snapshot: BrokerSnapshot) -> int:
    groups = set()
    for order in snapshot.working_orders:
        parts = order.order_ref.split(":")
        if len(parts) >= 5 and parts[0] == "A1":
            groups.add(":".join(parts[:-1]))
    return len(groups)


def _active_maturities(snapshot: BrokerSnapshot) -> set[str]:
    output = set()
    for order in snapshot.working_orders:
        parts = order.order_ref.split(":")
        if len(parts) >= 2 and parts[0] == "A1" and parts[1] in {"2Y", "5Y"}:
            output.add(parts[1])
    return output


def _target_for(target: DailyTarget | None, maturity: str) -> MaturityTarget:
    if target is None:
        return _zero_target()
    return target.for_maturity(maturity)  # type: ignore[arg-type]


def _states_for(snapshot: BrokerSnapshot, bindings: dict[str, BoundContract], maturity: str):
    swap = bindings[f"{maturity}:swap"]
    treasury = bindings[f"{maturity}:treasury"]
    return snapshot.position_state(swap.con_id), snapshot.position_state(treasury.con_id)


def _reconciliations(
    target: DailyTarget | None,
    snapshot: BrokerSnapshot,
    bindings: dict[str, BoundContract],
) -> dict[str, MaturityReconciliation]:
    output = {}
    for maturity in ("2Y", "5Y"):
        swap_state, treasury_state = _states_for(snapshot, bindings, maturity)
        output[maturity] = reconcile_maturity(
            target=_target_for(target, maturity),
            swap=swap_state,
            treasury=treasury_state,
        )
    return output


def _projected_positions(
    snapshot: BrokerSnapshot,
    bindings: dict[str, BoundContract],
    reconciliations: dict[str, MaturityReconciliation],
) -> dict[int, int]:
    projected = {
        con_id: snapshot.position_state(con_id).effective_qty
        for con_id in {binding.con_id for binding in bindings.values()}
    }
    for maturity, reconciliation in reconciliations.items():
        projected[bindings[f"{maturity}:swap"].con_id] += reconciliation.swap_delta
        projected[bindings[f"{maturity}:treasury"].con_id] += reconciliation.treasury_delta
    return projected


def _cap_breaches(target: DailyTarget, config: object) -> tuple[str, ...]:
    checks = (
        ("2Y", "swap", target.target_2y.swap_qty, "max_2y_swap_contracts"),
        ("2Y", "treasury", target.target_2y.treasury_qty, "max_2y_treasury_contracts"),
        ("5Y", "swap", target.target_5y.swap_qty, "max_5y_swap_contracts"),
        ("5Y", "treasury", target.target_5y.treasury_qty, "max_5y_treasury_contracts"),
    )
    failures = []
    for maturity, leg, quantity, field in checks:
        cap = getattr(config, field)
        if abs(quantity) > cap:
            failures.append(f"contract_cap:{maturity}:{leg}")
    return tuple(failures)


def _build_groups(
    *,
    target: DailyTarget | None,
    target_version: str,
    snapshot: BrokerSnapshot,
    bindings: dict[str, BoundContract],
    reconciliations: dict[str, MaturityReconciliation],
    config: object,
    now: datetime,
    session_order_groups: int,
    skip_active_maturities: bool,
    group_sequence: int | None = None,
) -> tuple[OrderGroupPlan, ...]:
    groups = []
    sequence = session_order_groups if group_sequence is None else group_sequence
    active = _active_maturities(snapshot) if skip_active_maturities else set()

    for maturity in ("2Y", "5Y"):
        if maturity in active:
            continue
        reconciliation = reconciliations[maturity]
        if reconciliation.is_noop:
            continue
        swap_binding = bindings[f"{maturity}:swap"]
        treasury_binding = bindings[f"{maturity}:treasury"]
        swap_quote = snapshot.quotes.get(swap_binding.con_id)
        treasury_quote = snapshot.quotes.get(treasury_binding.con_id)
        if swap_quote is None or treasury_quote is None:
            continue
        swap_state, treasury_state = _states_for(snapshot, bindings, maturity)
        sequence += 1
        group = build_order_group(
            maturity=maturity,
            target_version=target_version,
            sequence=sequence,
            reconciliation=reconciliation,
            swap_state=swap_state,
            treasury_state=treasury_state,
            bindings={"swap": swap_binding, "treasury": treasury_binding},
            quotes={"swap": swap_quote, "treasury": treasury_quote},
            created_at=now,
            timeout_seconds=getattr(config, "order_group_timeout_seconds"),
        )
        if group is not None:
            groups.append(group)
    return tuple(groups)


def _flatten_plan(
    *,
    snapshot: BrokerSnapshot,
    bindings: dict[str, BoundContract],
    risks: dict[int, ContractRisk],
    config: object,
    now: datetime,
    session_order_groups: int,
    reasons: tuple[str, ...],
    risk_decision: Any | None,
    group_sequence: int | None = None,
) -> CyclePlan:
    flat_reconciliations = _reconciliations(None, snapshot, bindings)
    effective = _projected_positions(snapshot, bindings, flat_reconciliations)
    exposure = calculate_portfolio_dv01(effective, risks)
    if snapshot.working_orders:
        return CyclePlan(
            action="cancel_then_flatten",
            reason_codes=reasons,
            groups=(),
            projected_dv01=exposure,
            reconciliations=flat_reconciliations,
            risk_decision=risk_decision,
        )
    expected = {binding.con_id for binding in bindings.values()}
    quotes = assess_quotes(
        snapshot,
        now=now,
        max_age_seconds=getattr(config, "max_quote_age_seconds"),
    )
    if set(snapshot.quotes) != expected or not (
        quotes.data_fresh and quotes.bid_ask_valid and quotes.market_fields_valid
    ):
        return CyclePlan(
            action="flatten_wait",
            reason_codes=reasons,
            groups=(),
            projected_dv01=exposure,
            reconciliations=flat_reconciliations,
            risk_decision=risk_decision,
        )
    groups = _build_groups(
        target=None,
        target_version=f"flatten:{now.date().isoformat()}",
        snapshot=snapshot,
        bindings=bindings,
        reconciliations=flat_reconciliations,
        config=config,
        now=now,
        session_order_groups=session_order_groups,
        skip_active_maturities=False,
        group_sequence=group_sequence,
    )
    return CyclePlan(
        action="flatten" if groups else "hold",
        reason_codes=reasons,
        groups=groups,
        projected_dv01=exposure,
        reconciliations=flat_reconciliations,
        risk_decision=risk_decision,
    )


def plan_cycle(
    *,
    target: DailyTarget | None,
    target_error: str | None,
    snapshot: BrokerSnapshot,
    bindings: dict[str, BoundContract],
    risks: dict[int, ContractRisk],
    config: object,
    now: datetime,
    session_order_groups: int,
    evaluator: Callable[..., Any],
    group_sequence: int | None = None,
    stop_requested: bool = False,
    margin_reserve_ok: bool = True,
    reconciled: bool = True,
    roll_allowed: bool = True,
    capacity_scale: Decimal = Decimal("1"),
    session_pnl_usd: Decimal = Decimal("0"),
    drawdown_usd: Decimal = Decimal("0"),
) -> CyclePlan:
    if now.utcoffset() is None:
        raise ValueError("Supervisor cycle time must include a timezone.")
    has_open = _has_open_position(snapshot)

    if target is None or target_error is not None:
        reasons = ("target_invalid",) + ((str(target_error),) if target_error else ())
        if has_open:
            return _flatten_plan(
                snapshot=snapshot,
                bindings=bindings,
                risks=risks,
                config=config,
                now=now,
                session_order_groups=session_order_groups,
                reasons=reasons,
                risk_decision=None,
                group_sequence=group_sequence,
            )
        zero_reconciliations = _reconciliations(None, snapshot, bindings)
        return CyclePlan(
            action="blocked",
            reason_codes=reasons,
            groups=(),
            projected_dv01=calculate_portfolio_dv01(snapshot.positions, risks),
            reconciliations=zero_reconciliations,
        )

    cap_breaches = _cap_breaches(target, config)
    if cap_breaches:
        reasons = ("contract_cap",) + cap_breaches
        if has_open:
            return _flatten_plan(
                snapshot=snapshot,
                bindings=bindings,
                risks=risks,
                config=config,
                now=now,
                session_order_groups=session_order_groups,
                reasons=reasons,
                risk_decision=None,
                group_sequence=group_sequence,
            )
        reconciliations = _reconciliations(None, snapshot, bindings)
        return CyclePlan(
            action="blocked",
            reason_codes=reasons,
            groups=(),
            projected_dv01=calculate_portfolio_dv01(snapshot.positions, risks),
            reconciliations=reconciliations,
        )

    if stop_requested:
        return _flatten_plan(
            snapshot=snapshot,
            bindings=bindings,
            risks=risks,
            config=config,
            now=now,
            session_order_groups=session_order_groups,
            reasons=("operator_stop",),
            risk_decision=None,
            group_sequence=group_sequence,
        ) if has_open else CyclePlan(
            action="hold",
            reason_codes=("operator_stop",),
            groups=(),
            projected_dv01=calculate_portfolio_dv01(snapshot.positions, risks),
            reconciliations=_reconciliations(None, snapshot, bindings),
        )

    reconciliations = _reconciliations(target, snapshot, bindings)
    projected_positions = _projected_positions(snapshot, bindings, reconciliations)
    exposure = calculate_portfolio_dv01(projected_positions, risks)
    if exposure.gross > getattr(config, "max_gross_dv01"):
        reasons = ("gross_dv01_limit",)
        if has_open:
            return _flatten_plan(
                snapshot=snapshot, bindings=bindings, risks=risks, config=config,
                now=now, session_order_groups=session_order_groups,
                reasons=reasons, risk_decision=None, group_sequence=group_sequence,
            )
        return CyclePlan("blocked", reasons, (), exposure, reconciliations)

    quote_assessment = assess_quotes(
        snapshot,
        now=now,
        max_age_seconds=getattr(config, "max_quote_age_seconds"),
    )
    expected_quotes = {binding.con_id for binding in bindings.values()}
    all_quotes_present = set(snapshot.quotes) == expected_quotes
    risk_state = RuntimeRiskState(
        capacity_scale=capacity_scale,
        has_open_position=has_open,
        emergency_flatten=False,
        scheduled_flatten=False,
        data_fresh=quote_assessment.data_fresh and all_quotes_present,
        bid_ask_valid=quote_assessment.bid_ask_valid and all_quotes_present,
        market_fields_valid=quote_assessment.market_fields_valid and all_quotes_present,
        broker_connected=True,
        reconciled=reconciled,
        roll_allowed=roll_allowed,
        margin_reserve_ok=margin_reserve_ok,
        residual_fraction=exposure.residual_fraction,
        portfolio_gross_dv01_usd_per_bp=exposure.gross,
        portfolio_net_dv01_usd_per_bp=exposure.net,
        orders_submitted=session_order_groups,
        working_orders=_active_group_count(snapshot),
        session_pnl_usd=session_pnl_usd,
        drawdown_usd=drawdown_usd,
    )
    risk_decision = evaluate_runtime_risk(
        risk_state,
        config,
        evaluator=evaluator,
    )
    if risk_decision is None:
        reasons = ("invalid_risk_inputs",)
        if has_open:
            return _flatten_plan(
                snapshot=snapshot, bindings=bindings, risks=risks, config=config,
                now=now, session_order_groups=session_order_groups,
                reasons=reasons, risk_decision=None, group_sequence=group_sequence,
            )
        return CyclePlan("blocked", reasons, (), exposure, reconciliations)

    allowed = bool(getattr(risk_decision, "allowed", False))
    reasons = tuple(getattr(risk_decision, "reason_codes", ()) or ())
    if not allowed:
        if bool(getattr(risk_decision, "flatten_requested", False)) and has_open:
            return _flatten_plan(
                snapshot=snapshot, bindings=bindings, risks=risks, config=config,
                now=now, session_order_groups=session_order_groups,
                reasons=reasons, risk_decision=risk_decision, group_sequence=group_sequence,
            )
        return CyclePlan("blocked", reasons, (), exposure, reconciliations, risk_decision)

    groups = _build_groups(
        target=target,
        target_version=target.version,
        snapshot=snapshot,
        bindings=bindings,
        reconciliations=reconciliations,
        config=config,
        now=now,
        session_order_groups=session_order_groups,
        skip_active_maturities=True,
        group_sequence=group_sequence,
    )
    return CyclePlan(
        action="trade" if groups else "hold",
        reason_codes=reasons,
        groups=groups,
        projected_dv01=exposure,
        reconciliations=reconciliations,
        risk_decision=risk_decision,
    )
