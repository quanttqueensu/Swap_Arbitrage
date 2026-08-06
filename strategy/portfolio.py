"""Pure rank-first portfolio DV01 composition."""

from collections.abc import Sequence
from decimal import Decimal, localcontext

from .models import TargetPosition


def _targets_by_maturity(targets: object) -> dict[str, TargetPosition] | None:
    if isinstance(targets, str) or not isinstance(targets, Sequence):
        return None
    result: dict[str, TargetPosition] = {}
    for target in targets:
        if type(target) is not TargetPosition or target.maturity in result:
            return None
        try:
            TargetPosition(
                target.maturity,
                target.swap_instrument_id,
                target.treasury_instrument_id,
                target.swap_quantity_contracts,
                target.treasury_quantity_contracts,
                target.target_dv01_usd_per_bp,
                target.gross_dv01_usd_per_bp,
                target.residual_net_dv01_usd_per_bp,
                target.expected_turnover_contracts,
                target.expected_cost_usd,
                target.rounding_diagnostic,
                target.cap_diagnostic,
            )
        except (TypeError, ValueError):
            return None
        result[target.maturity] = target
    return result


def portfolio_dv01(targets: object) -> tuple[Decimal, Decimal] | None:
    """Return total gross and residual-net DV01 for unique targets."""
    values = _targets_by_maturity(targets)
    if values is None:
        return None
    with localcontext() as context:
        context.prec = 50
        return (
            sum((target.gross_dv01_usd_per_bp for target in values.values()), Decimal("0")),
            sum((target.residual_net_dv01_usd_per_bp for target in values.values()), Decimal("0")),
        )


def select_portfolio_targets(
    ranked_maturities: object,
    targets: object,
    max_portfolio_gross_dv01_usd_per_bp: object,
    max_portfolio_net_dv01_usd_per_bp: object,
) -> tuple[TargetPosition, ...] | None:
    """Select safe targets in the supplied P32 rank order."""
    values = _targets_by_maturity(targets)
    if (
        values is None
        or isinstance(ranked_maturities, str)
        or not isinstance(ranked_maturities, Sequence)
        or type(max_portfolio_gross_dv01_usd_per_bp) is not Decimal
        or not max_portfolio_gross_dv01_usd_per_bp.is_finite()
        or max_portfolio_gross_dv01_usd_per_bp < 0
        or type(max_portfolio_net_dv01_usd_per_bp) is not Decimal
        or not max_portfolio_net_dv01_usd_per_bp.is_finite()
        or max_portfolio_net_dv01_usd_per_bp < 0
    ):
        return None
    if (
        any(type(maturity) is not str or not maturity.strip() for maturity in ranked_maturities)
        or len(set(ranked_maturities)) != len(ranked_maturities)
        or any(maturity not in values for maturity in ranked_maturities)
    ):
        return None
    with localcontext() as context:
        context.prec = 50
        selected: list[TargetPosition] = []
        gross = Decimal("0")
        net = Decimal("0")
        for maturity in ranked_maturities:
            target = values[maturity]
            candidate_gross = gross + target.gross_dv01_usd_per_bp
            candidate_net = net + target.residual_net_dv01_usd_per_bp
            if (
                candidate_gross <= max_portfolio_gross_dv01_usd_per_bp
                and candidate_net.copy_abs() <= max_portfolio_net_dv01_usd_per_bp
            ):
                selected.append(target)
                gross = candidate_gross
                net = candidate_net
        return tuple(selected)
