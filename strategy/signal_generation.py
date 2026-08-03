"""Causal signal features."""

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal, localcontext

from .models import SpreadObservation


def causal_zscore(current: SpreadObservation, prior: object) -> Decimal | None:
    """Return the 252-observation causal z-score, or ``None`` when unusable."""
    if type(current) is not SpreadObservation:
        return None
    if current.observation_time_utc.utcoffset() != timedelta(0):
        return None
    if isinstance(prior, str) or not isinstance(prior, Sequence) or len(prior) != 252:
        return None

    values: list[Decimal] = []
    previous_time = None
    for observation in prior:
        if type(observation) is not SpreadObservation:
            return None
        if observation.maturity != current.maturity or observation.source_quality_ok is not True:
            return None
        if observation.observation_time_utc.utcoffset() != timedelta(0):
            return None
        if observation.observation_time_utc >= current.observation_time_utc:
            return None
        if previous_time is not None and observation.observation_time_utc <= previous_time:
            return None
        previous_time = observation.observation_time_utc
        values.append(observation.gross_excess_spread_bps)

    with localcontext() as context:
        context.prec = 50
        mean = sum(values, Decimal("0")) / Decimal(252)
        variance = sum(((value - mean) ** 2 for value in values), Decimal("0")) / Decimal(251)
        if variance == 0:
            return None
        return (current.gross_excess_spread_bps - mean) / variance.sqrt()
