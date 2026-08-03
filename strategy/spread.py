"""Pure Decimal equations for the approved P31 spread calculations."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP, localcontext
import re

from .models import TradeDirection


STRATEGY_SPEC_VERSION = "p10.strategy-equations.v1"
_FULL_CONTRACT_ID = re.compile(r"(?:YIT|YIW|ZT|ZF)[HMUZ]\d{2}")


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


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return numerator / denominator


def rate_decimal_to_bps(rate_decimal: object) -> Decimal | None:
    rate = _decimal(rate_decimal)
    if rate is None:
        return None
    with localcontext() as context:
        context.prec = 50
        return rate * Decimal("10000")


def treasury_fractional_quote_to_points(
    whole_points: object, thirty_seconds: object, eighths_of_32nd: object
) -> Decimal | None:
    if (
        type(whole_points) is not int
        or type(thirty_seconds) is not int
        or type(eighths_of_32nd) is not int
        or whole_points < 0
        or not 0 <= thirty_seconds < 32
        or not 0 <= eighths_of_32nd < 8
    ):
        return None
    with localcontext() as context:
        context.prec = 50
        return Decimal(whole_points) + _divide(
            Decimal(thirty_seconds * 8 + eighths_of_32nd), Decimal("256")
        )


def tick_value_usd(
    minimum_increment_points: object, multiplier_usd_per_point: object
) -> Decimal | None:
    increment = _decimal(minimum_increment_points, positive=True)
    multiplier = _decimal(multiplier_usd_per_point, positive=True)
    if increment is None or multiplier is None:
        return None
    with localcontext() as context:
        context.prec = 50
        return increment * multiplier


def fixed_swap_spread_bps(swap_rate_bps: object, treasury_rate_bps: object) -> Decimal | None:
    swap_rate = _decimal(swap_rate_bps)
    treasury_rate = _decimal(treasury_rate_bps)
    if swap_rate is None or treasury_rate is None:
        return None
    with localcontext() as context:
        context.prec = 50
        return swap_rate - treasury_rate


def funding_spread_bps(floating_rate_bps: object, repo_rate_bps: object) -> Decimal | None:
    floating_rate = _decimal(floating_rate_bps)
    repo_rate = _decimal(repo_rate_bps)
    if floating_rate is None or repo_rate is None:
        return None
    with localcontext() as context:
        context.prec = 50
        return floating_rate - repo_rate


def expected_funding_bps(consecutive_lagged_history_bps: object) -> Decimal | None:
    if not isinstance(consecutive_lagged_history_bps, Sequence) or isinstance(
        consecutive_lagged_history_bps, str
    ):
        return None
    history = consecutive_lagged_history_bps
    if len(history) < 40:
        return None
    if any(_decimal(value) is None for value in history):
        return None
    recent = history[-60:]
    with localcontext() as context:
        context.prec = 50
        return _divide(sum(recent, Decimal("0")), Decimal(len(recent)))


def gross_excess_spread_bps(swap_spread_bps: object, expected_funding_bps: object) -> Decimal | None:
    swap_spread = _decimal(swap_spread_bps)
    funding = _decimal(expected_funding_bps)
    if swap_spread is None or funding is None:
        return None
    with localcontext() as context:
        context.prec = 50
        return swap_spread - funding


def directional_cost_buffer_bps(
    swap_bid_ask_usd: object,
    treasury_bid_ask_usd: object,
    commission_exchange_usd: object,
    slippage_usd: object,
    roll_usd: object,
    financing_not_in_funding_usd: object,
    cost_base_dv01_usd_per_bp: object,
) -> Decimal | None:
    costs = (
        _decimal(swap_bid_ask_usd, nonnegative=True),
        _decimal(treasury_bid_ask_usd, nonnegative=True),
        _decimal(commission_exchange_usd, nonnegative=True),
        _decimal(slippage_usd, nonnegative=True),
        _decimal(roll_usd, nonnegative=True),
        _decimal(financing_not_in_funding_usd, nonnegative=True),
    )
    base = _decimal(cost_base_dv01_usd_per_bp, positive=True)
    if base is None or any(cost is None for cost in costs):
        return None
    with localcontext() as context:
        context.prec = 50
        return _divide(sum(costs, Decimal("0")), base)


def net_opportunity_bps(
    direction: object, gross_excess_bps: object, cost_buffer_bps: object
) -> Decimal | None:
    gross = _decimal(gross_excess_bps)
    cost = _decimal(cost_buffer_bps, nonnegative=True)
    if (
        type(direction) is not TradeDirection
        or direction is TradeDirection.FLAT
        or gross is None
        or cost is None
    ):
        return None
    with localcontext() as context:
        context.prec = 50
        return Decimal(direction) * gross - cost


def dv01_hedge_quantities(
    direction: object,
    target_dv01: object,
    swap_dv01: object,
    treasury_dv01: object,
) -> tuple[int, int]:
    target = _decimal(target_dv01, positive=True)
    swap = _decimal(swap_dv01, positive=True)
    treasury = _decimal(treasury_dv01, positive=True)
    if (
        type(direction) is not TradeDirection
        or direction is TradeDirection.FLAT
        or target is None
        or swap is None
        or treasury is None
    ):
        return (0, 0)
    with localcontext() as context:
        context.prec = 50
        swap_magnitude = int((target / swap).to_integral_value(rounding=ROUND_HALF_UP))
        if swap_magnitude == 0:
            return (0, 0)
        swap_quantity = int(direction) * swap_magnitude
        continuous_treasury = -Decimal(swap_quantity) * swap / treasury
        floor = int(continuous_treasury.to_integral_value(rounding=ROUND_FLOOR))
        candidates = (floor, floor + 1)
        treasury_quantity = min(
            candidates,
            key=lambda quantity: (
                abs(-Decimal(swap_quantity) * swap - Decimal(quantity) * treasury),
                Decimal(abs(swap_quantity)) * swap + Decimal(abs(quantity)) * treasury,
                quantity,
            ),
        )
        return (swap_quantity, treasury_quantity)


def residual_dv01_usd_per_bp(
    swap_quantity: object,
    treasury_quantity: object,
    swap_dv01: object,
    treasury_dv01: object,
) -> Decimal | None:
    swap = _decimal(swap_dv01, positive=True)
    treasury = _decimal(treasury_dv01, positive=True)
    if (
        type(swap_quantity) is not int
        or type(treasury_quantity) is not int
        or swap is None
        or treasury is None
    ):
        return None
    with localcontext() as context:
        context.prec = 50
        return -Decimal(swap_quantity) * swap - Decimal(treasury_quantity) * treasury


def residual_fraction(net_dv01: object, target_dv01: object) -> Decimal | None:
    net = _decimal(net_dv01)
    target = _decimal(target_dv01, positive=True)
    if net is None or target is None:
        return None
    return _divide(abs(net), target)


def basket_pnl_usd(legs: object, total_cost_usd: object) -> Decimal | None:
    total_cost = _decimal(total_cost_usd, nonnegative=True)
    if (
        not isinstance(legs, Sequence)
        or isinstance(legs, str)
        or not legs
        or total_cost is None
    ):
        return None
    validated_legs = []
    for leg in legs:
        if type(leg) is not tuple or len(leg) != 6:
            return None
        start_id, end_id, quantity, multiplier, start_price, end_price = leg
        if (
            type(start_id) is not str
            or type(end_id) is not str
            or _FULL_CONTRACT_ID.fullmatch(start_id) is None
            or _FULL_CONTRACT_ID.fullmatch(end_id) is None
            or start_id != end_id
            or type(quantity) is not int
        ):
            return None
        multiplier_decimal = _decimal(multiplier, positive=True)
        start_decimal = _decimal(start_price, positive=True)
        end_decimal = _decimal(end_price, positive=True)
        if multiplier_decimal is None or start_decimal is None or end_decimal is None:
            return None
        validated_legs.append((quantity, multiplier_decimal, start_decimal, end_decimal))
    with localcontext() as context:
        context.prec = 50
        return (
            sum(
                (
                    Decimal(quantity) * multiplier * (end_price - start_price)
                    for quantity, multiplier, start_price, end_price in validated_legs
                ),
                Decimal("0"),
            )
            - total_cost
        )


def contract_turnover_contracts(quantities: object) -> int | None:
    if not isinstance(quantities, Sequence) or isinstance(quantities, str):
        return None
    if any(type(quantity) is not int for quantity in quantities):
        return None
    return sum(abs(quantity) for quantity in quantities)
