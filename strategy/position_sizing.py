from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR, localcontext

from .models import TargetPosition, TradeDirection
from .spread import dv01_hedge_quantities, residual_dv01_usd_per_bp, residual_fraction


SIZING_RISK_VERSION = "p33.position-sizing-risk.v1"
VOLATILITY_LOOKBACK = 63
MAX_RESIDUAL_FRACTION = Decimal("0.05")


def _decimal(value: object, *, positive: bool = False, nonnegative: bool = False) -> Decimal | None:
    if type(value) is not Decimal or not value.is_finite():
        return None
    if positive and value <= 0:
        return None
    if nonnegative and value < 0:
        return None
    return value


def _scale(value: object) -> Decimal | None:
    decimal = _decimal(value, nonnegative=True)
    return decimal if decimal is not None and decimal <= 1 else None


def _utc(value: object) -> datetime | None:
    return value if type(value) is datetime and value.utcoffset() == timedelta(0) else None


def volatility_scale(
    decision_time_utc: object,
    current_realized_vol: object,
    prior_realized_vols: object,
) -> Decimal | None:
    decision_time = _utc(decision_time_utc)
    current = _decimal(current_realized_vol, positive=True)
    if (
        decision_time is None
        or current is None
        or not isinstance(prior_realized_vols, Sequence)
        or isinstance(prior_realized_vols, str)
        or len(prior_realized_vols) != VOLATILITY_LOOKBACK
    ):
        return None
    timestamps = []
    values = []
    for item in prior_realized_vols:
        if type(item) is not tuple or len(item) != 2:
            return None
        timestamp, value = item
        timestamp = _utc(timestamp)
        value = _decimal(value, positive=True)
        if timestamp is None or value is None or timestamp >= decision_time:
            return None
        timestamps.append(timestamp)
        values.append(value)
    if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
        return None
    median = sorted(values)[VOLATILITY_LOOKBACK // 2]
    with localcontext() as context:
        context.prec = 50
        context.rounding = "ROUND_HALF_EVEN"
        return min(Decimal("1"), median / current)


def signal_strength_scale(z_score: object) -> Decimal | None:
    z = _decimal(z_score)
    if z is None:
        return None
    with localcontext() as context:
        context.prec = 50
        context.rounding = "ROUND_HALF_EVEN"
        return min(Decimal("1"), z.copy_abs() / Decimal("2"))


def liquidity_scale(
    swap_quantity: object,
    treasury_quantity: object,
    swap_available_contracts: object,
    treasury_available_contracts: object,
) -> Decimal | None:
    if (
        type(swap_quantity) is not int
        or type(treasury_quantity) is not int
        or not swap_quantity
        or not treasury_quantity
        or type(swap_available_contracts) is not int
        or type(treasury_available_contracts) is not int
        or swap_available_contracts < 0
        or treasury_available_contracts < 0
    ):
        return None
    with localcontext() as context:
        context.prec = 50
        context.rounding = "ROUND_HALF_EVEN"
        return min(
            Decimal("1"),
            Decimal(swap_available_contracts) / Decimal(abs(swap_quantity)),
            Decimal(treasury_available_contracts) / Decimal(abs(treasury_quantity)),
        )


def scaled_target_dv01(
    base_target: object,
    volatility: object,
    strength: object,
    liquidity: object,
) -> Decimal | None:
    base = _decimal(base_target, positive=True)
    scales = (_scale(volatility), _scale(strength), _scale(liquidity))
    if base is None or any(scale is None for scale in scales):
        return None
    with localcontext() as context:
        context.prec = 50
        context.rounding = "ROUND_HALF_EVEN"
        return base * scales[0] * scales[1] * scales[2]


def _bounded_target(
    *,
    direction: object,
    liquid_target: object,
    swap_dv01: object,
    treasury_dv01: object,
    swap_available_contracts: object,
    treasury_available_contracts: object,
    max_swap_contracts: object,
    max_treasury_contracts: object,
    available_gross: object,
) -> tuple[Decimal, int, int, Decimal, Decimal] | None:
    target = _decimal(liquid_target, positive=True)
    swap = _decimal(swap_dv01, positive=True)
    treasury = _decimal(treasury_dv01, positive=True)
    if (
        type(direction) is not TradeDirection
        or direction is TradeDirection.FLAT
        or target is None
        or swap is None
        or treasury is None
        or any(
            type(value) is not int or value < 0
            for value in (
                swap_available_contracts,
                treasury_available_contracts,
                max_swap_contracts,
                max_treasury_contracts,
            )
        )
        or _decimal(available_gross, nonnegative=True) is None
    ):
        return None
    with localcontext() as context:
        context.prec = 50
        maximum = int((target / swap).to_integral_value(rounding=ROUND_FLOOR))
        maximum = min(maximum, swap_available_contracts)
        if max_swap_contracts:
            maximum = min(maximum, max_swap_contracts)
        maximum = min(
            maximum,
            int((available_gross / swap).to_integral_value(rounding=ROUND_FLOOR)),
        )
        for magnitude in range(maximum, 0, -1):
            candidate = Decimal(magnitude) * swap
            swap_quantity, treasury_quantity = dv01_hedge_quantities(
                direction, candidate, swap, treasury
            )
            residual = residual_dv01_usd_per_bp(
                swap_quantity, treasury_quantity, swap, treasury
            )
            fraction = residual_fraction(residual, candidate)
            gross = (
                Decimal(abs(swap_quantity)) * swap
                + Decimal(abs(treasury_quantity)) * treasury
            )
            if (
                not swap_quantity
                or not treasury_quantity
                or abs(treasury_quantity) > treasury_available_contracts
                or (max_treasury_contracts and abs(treasury_quantity) > max_treasury_contracts)
                or gross > available_gross
                or fraction is None
                or fraction > MAX_RESIDUAL_FRACTION
            ):
                continue
            return candidate, swap_quantity, treasury_quantity, gross, residual
    return None


def build_target_position(
    *,
    maturity: object,
    swap_instrument_id: object,
    treasury_instrument_id: object,
    direction: object,
    base_target_dv01_usd_per_bp: object,
    decision_time_utc: object,
    current_realized_vol: object,
    prior_realized_vols: object,
    z_score: object,
    swap_available_contracts: object,
    treasury_available_contracts: object,
    swap_dv01_usd_per_bp: object,
    treasury_dv01_usd_per_bp: object,
    current_swap_quantity_contracts: object,
    current_treasury_quantity_contracts: object,
    max_swap_contracts: object,
    max_treasury_contracts: object,
    available_gross_dv01_usd_per_bp: object,
    expected_cost_usd: object,
) -> TargetPosition | None:
    if (
        any(
            type(value) is not str or not value.strip()
            for value in (maturity, swap_instrument_id, treasury_instrument_id)
        )
        or type(current_swap_quantity_contracts) is not int
        or type(current_treasury_quantity_contracts) is not int
        or _decimal(expected_cost_usd, nonnegative=True) is None
    ):
        return None
    vol = volatility_scale(
        decision_time_utc, current_realized_vol, prior_realized_vols
    )
    strength = signal_strength_scale(z_score)
    pre_liquidity_target = scaled_target_dv01(
        base_target_dv01_usd_per_bp, vol, strength, Decimal("1")
    )
    provisional = dv01_hedge_quantities(
        direction,
        pre_liquidity_target,
        swap_dv01_usd_per_bp,
        treasury_dv01_usd_per_bp,
    )
    liquidity = liquidity_scale(
        *provisional, swap_available_contracts, treasury_available_contracts
    )
    liquid_target = scaled_target_dv01(
        base_target_dv01_usd_per_bp, vol, strength, liquidity
    )
    bounded = _bounded_target(
        direction=direction,
        liquid_target=liquid_target,
        swap_dv01=swap_dv01_usd_per_bp,
        treasury_dv01=treasury_dv01_usd_per_bp,
        swap_available_contracts=swap_available_contracts,
        treasury_available_contracts=treasury_available_contracts,
        max_swap_contracts=max_swap_contracts,
        max_treasury_contracts=max_treasury_contracts,
        available_gross=available_gross_dv01_usd_per_bp,
    )
    if bounded is None:
        return None
    target, swap_quantity, treasury_quantity, gross, residual = bounded
    with localcontext() as context:
        context.prec = 50
        unrestricted = _decimal(liquid_target, positive=True)
        swap = _decimal(swap_dv01_usd_per_bp, positive=True)
        if unrestricted is None or swap is None:
            return None
        whole_swap_target = (
            (unrestricted / swap).to_integral_value(rounding=ROUND_FLOOR) * swap
        )
    return TargetPosition(
        maturity=maturity,
        swap_instrument_id=swap_instrument_id,
        treasury_instrument_id=treasury_instrument_id,
        swap_quantity_contracts=swap_quantity,
        treasury_quantity_contracts=treasury_quantity,
        target_dv01_usd_per_bp=target,
        gross_dv01_usd_per_bp=gross,
        residual_net_dv01_usd_per_bp=residual,
        expected_turnover_contracts=(
            abs(swap_quantity - current_swap_quantity_contracts)
            + abs(treasury_quantity - current_treasury_quantity_contracts)
        ),
        expected_cost_usd=expected_cost_usd,
        rounding_diagnostic="minimum_residual",
        cap_diagnostic=(
            "scaled_to_capacity" if target < whole_swap_target else "within_capacity"
        ),
    )
