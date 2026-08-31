from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib


LIVE_SIGNAL_STRATEGY_VERSION = "daily_fred_cmt_v1"
MIN_OBSERVATIONS = 252
Z_ENTRY = Decimal("2.0")
Z_EXIT = Decimal("0.5")


def _finite_decimal(value: object) -> bool:
    return type(value) is Decimal and value.is_finite()


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


@dataclass(frozen=True)
class DailySpreadObservation:
    maturity: str
    observed_at: datetime
    eris_rate_bps: Decimal
    fred_series: str
    treasury_rate_bps: Decimal
    spread_bps: Decimal


@dataclass(frozen=True)
class HistoricalModelState:
    version: str
    mean_bps: Decimal
    std_bps: Decimal
    observation_count: int


@dataclass(frozen=True)
class LiveSignalResult:
    maturity: str
    strategy_version: str
    snapshot_id: str
    mid_spread_bps: Decimal | None
    spread_bid_side_bps: Decimal | None
    spread_ask_side_bps: Decimal | None
    historical_mean_bps: Decimal | None
    historical_std_bps: Decimal | None
    z_score: Decimal | None
    prior_state: int
    state: int
    blocked: bool
    reason_codes: tuple[str, ...]


def _snapshot_id(
    observation: DailySpreadObservation,
    model: HistoricalModelState,
    prior_state: int,
) -> str:
    observed_at = (
        observation.observed_at.astimezone(timezone.utc).isoformat()
        if _aware(observation.observed_at)
        else "<invalid>"
    )
    fields = (
        LIVE_SIGNAL_STRATEGY_VERSION,
        observation.maturity,
        observed_at,
        str(observation.eris_rate_bps),
        observation.fred_series,
        str(observation.treasury_rate_bps),
        str(observation.spread_bps),
        model.version,
        str(model.mean_bps),
        str(model.std_bps),
        str(model.observation_count),
        str(prior_state),
    )
    return hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()


def _transition(prior_state: int, z_score: Decimal) -> int:
    if prior_state not in {-1, 0, 1}:
        raise ValueError("prior_state must be -1, 0, or 1")
    if prior_state == 0:
        if z_score <= -Z_ENTRY:
            return 1
        if z_score >= Z_ENTRY:
            return -1
        return 0
    if prior_state == 1:
        if z_score >= Z_ENTRY:
            return -1
        return 0 if z_score >= -Z_EXIT else 1
    if z_score <= -Z_ENTRY:
        return 1
    return 0 if z_score <= Z_EXIT else -1


def evaluate_daily_signal(
    *,
    observation: DailySpreadObservation,
    model: HistoricalModelState,
    prior_state: int,
) -> LiveSignalResult:
    if prior_state not in {-1, 0, 1}:
        raise ValueError("prior_state must be -1, 0, or 1")

    reasons: list[str] = []
    expected_series = {"2Y": "DGS2", "5Y": "DGS5"}.get(observation.maturity)
    if expected_series is None or observation.fred_series != expected_series:
        reasons.append("invalid_fred_series")
    if not _aware(observation.observed_at):
        reasons.append("invalid_observation_timestamp")
    values = (
        observation.eris_rate_bps,
        observation.treasury_rate_bps,
        observation.spread_bps,
    )
    if not all(_finite_decimal(value) for value in values):
        reasons.append("invalid_daily_observation")
    elif observation.spread_bps != observation.eris_rate_bps - observation.treasury_rate_bps:
        reasons.append("misaligned_spread_components")
    if model.version != LIVE_SIGNAL_STRATEGY_VERSION:
        reasons.append("model_version_mismatch")
    if type(model.observation_count) is not int or model.observation_count < MIN_OBSERVATIONS:
        reasons.append("insufficient_history")
    if not _finite_decimal(model.mean_bps):
        reasons.append("invalid_historical_mean")
    if not _finite_decimal(model.std_bps) or model.std_bps <= 0:
        reasons.append("invalid_historical_std")

    snapshot_id = _snapshot_id(observation, model, prior_state)
    if reasons:
        return LiveSignalResult(
            maturity=observation.maturity,
            strategy_version=LIVE_SIGNAL_STRATEGY_VERSION,
            snapshot_id=snapshot_id,
            mid_spread_bps=observation.spread_bps if _finite_decimal(observation.spread_bps) else None,
            spread_bid_side_bps=None,
            spread_ask_side_bps=None,
            historical_mean_bps=model.mean_bps if _finite_decimal(model.mean_bps) else None,
            historical_std_bps=model.std_bps if _finite_decimal(model.std_bps) else None,
            z_score=None,
            prior_state=prior_state,
            state=0,
            blocked=True,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )

    with localcontext() as context:
        context.prec = 50
        z_score = (observation.spread_bps - model.mean_bps) / model.std_bps

    return LiveSignalResult(
        maturity=observation.maturity,
        strategy_version=LIVE_SIGNAL_STRATEGY_VERSION,
        snapshot_id=snapshot_id,
        mid_spread_bps=observation.spread_bps,
        spread_bid_side_bps=None,
        spread_ask_side_bps=None,
        historical_mean_bps=model.mean_bps,
        historical_std_bps=model.std_bps,
        z_score=z_score,
        prior_state=prior_state,
        state=_transition(prior_state, z_score),
        blocked=False,
        reason_codes=("within_daily_model",),
    )
