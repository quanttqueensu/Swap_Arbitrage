from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable


@dataclass(frozen=True)
class RuntimeRiskState:
    capacity_scale: Decimal
    has_open_position: bool
    emergency_flatten: bool
    scheduled_flatten: bool
    data_fresh: bool
    bid_ask_valid: bool
    market_fields_valid: bool
    broker_connected: bool
    reconciled: bool
    roll_allowed: bool
    margin_reserve_ok: bool
    residual_fraction: Decimal
    portfolio_gross_dv01_usd_per_bp: Decimal
    portfolio_net_dv01_usd_per_bp: Decimal
    orders_submitted: int
    working_orders: int
    session_pnl_usd: Decimal
    drawdown_usd: Decimal

    @classmethod
    def safe_defaults(cls) -> "RuntimeRiskState":
        return cls(
            capacity_scale=Decimal("1"),
            has_open_position=False,
            emergency_flatten=False,
            scheduled_flatten=False,
            data_fresh=True,
            bid_ask_valid=True,
            market_fields_valid=True,
            broker_connected=True,
            reconciled=True,
            roll_allowed=True,
            margin_reserve_ok=True,
            residual_fraction=Decimal("0"),
            portfolio_gross_dv01_usd_per_bp=Decimal("0"),
            portfolio_net_dv01_usd_per_bp=Decimal("0"),
            orders_submitted=0,
            working_orders=0,
            session_pnl_usd=Decimal("0"),
            drawdown_usd=Decimal("0"),
        )


def _default_evaluator() -> Callable[..., Any] | None:
    try:
        from strategy.risk_signals import evaluate_risk
    except Exception:
        return None
    return evaluate_risk


def evaluate_runtime_risk(
    state: RuntimeRiskState,
    config: object,
    *,
    evaluator: Callable[..., Any] | None = None,
) -> Any | None:
    if type(state) is not RuntimeRiskState:
        return None

    active_evaluator = evaluator or _default_evaluator()
    if active_evaluator is None:
        return None

    try:
        return active_evaluator(
            capacity_scale=state.capacity_scale,
            has_open_position=state.has_open_position,
            emergency_flatten=state.emergency_flatten,
            scheduled_flatten=state.scheduled_flatten,
            data_fresh=state.data_fresh,
            bid_ask_valid=state.bid_ask_valid,
            market_fields_valid=state.market_fields_valid,
            broker_connected=state.broker_connected,
            reconciled=state.reconciled,
            roll_allowed=state.roll_allowed,
            margin_reserve_ok=state.margin_reserve_ok,
            residual_fraction=state.residual_fraction,
            max_residual_fraction=getattr(config, "max_residual_dv01_fraction"),
            portfolio_gross_dv01_usd_per_bp=state.portfolio_gross_dv01_usd_per_bp,
            max_portfolio_gross_dv01_usd_per_bp=getattr(config, "max_gross_dv01"),
            portfolio_net_dv01_usd_per_bp=state.portfolio_net_dv01_usd_per_bp,
            max_portfolio_net_dv01_usd_per_bp=getattr(config, "max_net_dv01"),
            orders_submitted=state.orders_submitted,
            max_orders=getattr(config, "max_order_groups_per_session"),
            working_orders=state.working_orders,
            max_working_orders=getattr(config, "max_working_order_groups"),
            session_pnl_usd=state.session_pnl_usd,
            max_session_loss_usd=getattr(config, "max_session_loss_usd"),
            drawdown_usd=state.drawdown_usd,
            max_drawdown_usd=getattr(config, "max_drawdown_usd"),
        )
    except (AttributeError, TypeError, ValueError):
        return None
