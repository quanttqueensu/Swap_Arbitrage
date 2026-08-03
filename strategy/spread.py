"""Pure Decimal equations for the approved P31 spread calculations."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, localcontext

from .models import TradeDirection


STRATEGY_SPEC_VERSION = "p10.strategy-equations.v1"


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
    return None if rate is None else rate * Decimal("10000")


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
    return Decimal(whole_points) + _divide(
        Decimal(thirty_seconds * 8 + eighths_of_32nd), Decimal("256")
    )


def tick_value_usd(
    minimum_increment_points: object, multiplier_usd_per_point: object
) -> Decimal | None:
    increment = _decimal(minimum_increment_points, positive=True)
    multiplier = _decimal(multiplier_usd_per_point, positive=True)
    return None if increment is None or multiplier is None else increment * multiplier


def fixed_swap_spread_bps(swap_rate_bps: object, treasury_rate_bps: object) -> Decimal | None:
    swap_rate = _decimal(swap_rate_bps)
    treasury_rate = _decimal(treasury_rate_bps)
    return None if swap_rate is None or treasury_rate is None else swap_rate - treasury_rate


def funding_spread_bps(floating_rate_bps: object, repo_rate_bps: object) -> Decimal | None:
    floating_rate = _decimal(floating_rate_bps)
    repo_rate = _decimal(repo_rate_bps)
    return None if floating_rate is None or repo_rate is None else floating_rate - repo_rate


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
    return _divide(sum(recent, Decimal("0")), Decimal(len(recent)))


def gross_excess_spread_bps(swap_spread_bps: object, expected_funding_bps: object) -> Decimal | None:
    swap_spread = _decimal(swap_spread_bps)
    funding = _decimal(expected_funding_bps)
    return None if swap_spread is None or funding is None else swap_spread - funding


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
    return Decimal(direction) * gross - cost
