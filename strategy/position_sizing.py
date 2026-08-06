from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal, localcontext


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
