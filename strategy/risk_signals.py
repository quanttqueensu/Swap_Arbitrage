from __future__ import annotations

from decimal import Decimal, localcontext

from .models import FlattenUrgency, NamedValue, RiskDecision


def _decimal(
    value: object, *, positive: bool = False, nonnegative: bool = False
) -> Decimal | None:
    if type(value) is not Decimal or not value.is_finite():
        return None
    if positive and value <= 0:
        return None
    if nonnegative and value < 0:
        return None
    return value


def evaluate_risk(
    *,
    capacity_scale: object,
    has_open_position: object,
    emergency_flatten: object,
    scheduled_flatten: object,
    data_fresh: object,
    bid_ask_valid: object,
    market_fields_valid: object,
    broker_connected: object,
    reconciled: object,
    roll_allowed: object,
    margin_reserve_ok: object,
    residual_fraction: object,
    max_residual_fraction: object,
    portfolio_gross_dv01_usd_per_bp: object,
    max_portfolio_gross_dv01_usd_per_bp: object,
    portfolio_net_dv01_usd_per_bp: object,
    max_portfolio_net_dv01_usd_per_bp: object,
    orders_submitted: object,
    max_orders: object,
    working_orders: object,
    max_working_orders: object,
    session_pnl_usd: object,
    max_session_loss_usd: object,
    drawdown_usd: object,
    max_drawdown_usd: object,
) -> RiskDecision | None:
    booleans = (
        has_open_position,
        emergency_flatten,
        scheduled_flatten,
        data_fresh,
        bid_ask_valid,
        market_fields_valid,
        broker_connected,
        reconciled,
        roll_allowed,
        margin_reserve_ok,
    )
    counters = (orders_submitted, max_orders, working_orders, max_working_orders)
    scale = _decimal(capacity_scale, nonnegative=True)
    residual = _decimal(residual_fraction, nonnegative=True)
    max_residual = _decimal(max_residual_fraction, nonnegative=True)
    gross = _decimal(portfolio_gross_dv01_usd_per_bp, nonnegative=True)
    max_gross = _decimal(max_portfolio_gross_dv01_usd_per_bp, nonnegative=True)
    net = _decimal(portfolio_net_dv01_usd_per_bp)
    max_net = _decimal(max_portfolio_net_dv01_usd_per_bp, nonnegative=True)
    session_pnl = _decimal(session_pnl_usd)
    max_loss = _decimal(max_session_loss_usd, positive=True)
    drawdown = _decimal(drawdown_usd, nonnegative=True)
    max_drawdown = _decimal(max_drawdown_usd, positive=True)
    if (
        any(type(value) is not bool for value in booleans)
        or any(type(value) is not int or value < 0 for value in counters)
        or scale is None
        or scale > 1
        or any(value is None for value in (
            residual, max_residual, gross, max_gross, net, max_net, session_pnl,
            max_loss, drawdown, max_drawdown,
        ))
        or gross > max_gross
    ):
        return None

    limits = (
        NamedValue("max_residual_fraction", max_residual, "fraction"),
        NamedValue("max_portfolio_gross_dv01", max_gross, "usd_per_bp"),
        NamedValue("max_portfolio_net_dv01", max_net, "usd_per_bp"),
        NamedValue("max_orders", Decimal(max_orders), "orders"),
        NamedValue("max_working_orders", Decimal(max_working_orders), "orders"),
        NamedValue("max_session_loss", max_loss, "usd"),
        NamedValue("max_drawdown", max_drawdown, "usd"),
    )
    measured_values = (
        NamedValue("capacity_scale", scale, "fraction"),
        NamedValue("portfolio_gross_dv01", gross, "usd_per_bp"),
        NamedValue("residual_fraction", residual, "fraction"),
        NamedValue("portfolio_net_dv01", net, "usd_per_bp"),
        NamedValue("orders_submitted", Decimal(orders_submitted), "orders"),
        NamedValue("working_orders", Decimal(working_orders), "orders"),
        NamedValue("session_pnl", session_pnl, "usd"),
        NamedValue("drawdown", drawdown, "usd"),
    )
    if emergency_flatten or scheduled_flatten:
        emergency = emergency_flatten
        return RiskDecision(
            allowed=False,
            scale=Decimal("0"),
            reason_codes=("emergency_flatten" if emergency else "scheduled_flatten",),
            flatten_requested=has_open_position,
            urgency=(
                (FlattenUrgency.EMERGENCY if emergency else FlattenUrgency.SCHEDULED)
                if has_open_position else FlattenUrgency.NONE
            ),
            limits=limits,
            measured_values=measured_values,
        )

    with localcontext() as context:
        context.prec = 50
        context.rounding = "ROUND_HALF_EVEN"
        reasons = tuple(
            reason
            for failed, reason in (
                (not data_fresh, "stale_market_data"),
                (not bid_ask_valid, "invalid_bid_ask"),
                (not market_fields_valid, "missing_or_nonpositive_market_field"),
                (not broker_connected, "broker_disconnected"),
                (not reconciled, "reconciliation_mismatch"),
                (not roll_allowed, "roll_restricted"),
                (-session_pnl >= max_loss, "session_loss_limit"),
                (drawdown >= max_drawdown, "drawdown_limit"),
                (not margin_reserve_ok, "margin_reserve_failure"),
                (residual > max_residual, "residual_dv01_limit"),
                (net.copy_abs() > max_net, "portfolio_net_dv01_limit"),
                (orders_submitted >= max_orders, "order_rate_limit"),
                (working_orders >= max_working_orders, "working_order_limit"),
            )
            if failed
        )
    if reasons:
        return RiskDecision(
            allowed=False,
            scale=Decimal("0"),
            reason_codes=reasons,
            flatten_requested=has_open_position,
            urgency=FlattenUrgency.EMERGENCY if has_open_position else FlattenUrgency.NONE,
            limits=limits,
            measured_values=measured_values,
        )
    return RiskDecision(
        allowed=True,
        scale=scale,
        reason_codes=("capacity_scaled" if scale < 1 else "within_limits",),
        flatten_requested=False,
        urgency=FlattenUrgency.NONE,
        limits=limits,
        measured_values=measured_values,
    )
