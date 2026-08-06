"""Pure itemized cost estimates for approved strategy inputs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from .models import NamedValue
from .spread import directional_cost_buffer_bps


@dataclass(frozen=True, slots=True)
class CostEstimate:
    components: tuple[NamedValue, ...]
    total_cost_usd: Decimal
    total_cost_bps: Decimal


def _cost_estimate(
    *,
    swap_bid_ask_usd: object,
    treasury_bid_ask_usd: object,
    commission_exchange_usd: object,
    slippage_usd: object,
    roll_close_usd: object,
    roll_open_usd: object,
    financing_not_in_funding_usd: object,
    cost_base_dv01_usd_per_bp: object,
) -> CostEstimate | None:
    values = (
        swap_bid_ask_usd,
        treasury_bid_ask_usd,
        commission_exchange_usd,
        slippage_usd,
        roll_close_usd,
        roll_open_usd,
        financing_not_in_funding_usd,
    )
    if (
        type(cost_base_dv01_usd_per_bp) is not Decimal
        or not cost_base_dv01_usd_per_bp.is_finite()
        or cost_base_dv01_usd_per_bp <= 0
        or any(
            type(value) is not Decimal or not value.is_finite() or value < 0
            for value in values
        )
    ):
        return None
    with localcontext() as context:
        context.prec = 50
        roll = values[4] + values[5]
        components = (
            NamedValue("swap_bid_ask", values[0], "usd"),
            NamedValue("treasury_bid_ask", values[1], "usd"),
            NamedValue("commission_exchange", values[2], "usd"),
            NamedValue("slippage", values[3], "usd"),
            NamedValue("roll", roll, "usd"),
            NamedValue("financing_not_in_funding", values[6], "usd"),
        )
        total_cost_bps = directional_cost_buffer_bps(
            *(component.value for component in components), cost_base_dv01_usd_per_bp
        )
        if total_cost_bps is None:
            return None
        return CostEstimate(
            components,
            sum((component.value for component in components), Decimal("0")),
            total_cost_bps,
        )


def naive_cost(
    *,
    swap_bid_ask_usd: object,
    treasury_bid_ask_usd: object,
    commission_exchange_usd: object,
    slippage_usd: object,
    roll_close_usd: object,
    roll_open_usd: object,
    financing_not_in_funding_usd: object,
    cost_base_dv01_usd_per_bp: object,
) -> CostEstimate | None:
    return _cost_estimate(
        swap_bid_ask_usd=swap_bid_ask_usd,
        treasury_bid_ask_usd=treasury_bid_ask_usd,
        commission_exchange_usd=commission_exchange_usd,
        slippage_usd=slippage_usd,
        roll_close_usd=roll_close_usd,
        roll_open_usd=roll_open_usd,
        financing_not_in_funding_usd=financing_not_in_funding_usd,
        cost_base_dv01_usd_per_bp=cost_base_dv01_usd_per_bp,
    )


def observed_cost(
    *,
    swap_bid_ask_usd: object,
    treasury_bid_ask_usd: object,
    commission_exchange_usd: object,
    slippage_usd: object,
    roll_close_usd: object,
    roll_open_usd: object,
    financing_not_in_funding_usd: object,
    cost_base_dv01_usd_per_bp: object,
) -> CostEstimate | None:
    return _cost_estimate(
        swap_bid_ask_usd=swap_bid_ask_usd,
        treasury_bid_ask_usd=treasury_bid_ask_usd,
        commission_exchange_usd=commission_exchange_usd,
        slippage_usd=slippage_usd,
        roll_close_usd=roll_close_usd,
        roll_open_usd=roll_open_usd,
        financing_not_in_funding_usd=financing_not_in_funding_usd,
        cost_base_dv01_usd_per_bp=cost_base_dv01_usd_per_bp,
    )
