from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib

from .eris_pricing import ErisParRateQuote


LIVE_SIGNAL_STRATEGY_VERSION = "live_yield_futures_v1"
MIN_OBSERVATIONS = 63
Z_ENTRY = Decimal("2.0")
Z_EXIT = Decimal("0.5")


def _finite_decimal(value: object) -> bool:
    return type(value) is Decimal and value.is_finite()


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


@dataclass(frozen=True)
class TreasuryYieldQuote:
    contract_id: str
    symbol: str
    observed_at: datetime
    bid_percent: Decimal
    ask_percent: Decimal
    mid_percent: Decimal

    @property
    def bid_bps(self) -> Decimal:
        return self.bid_percent * Decimal("100")

    @property
    def ask_bps(self) -> Decimal:
        return self.ask_percent * Decimal("100")

    @property
    def mid_bps(self) -> Decimal:
        return self.mid_percent * Decimal("100")


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
    maturity: str,
    eris: ErisParRateQuote,
    treasury: TreasuryYieldQuote,
    model: HistoricalModelState,
    prior_state: int,
) -> str:
    def timestamp(value: object) -> str:
        if _aware(value):
            return value.astimezone(timezone.utc).isoformat()
        return "<invalid>"

    fields = (
        maturity,
        eris.contract_id,
        timestamp(eris.observed_at),
        str(eris.bid_par_rate_bps),
        str(eris.ask_par_rate_bps),
        str(eris.mid_par_rate_bps),
        treasury.contract_id,
        timestamp(treasury.observed_at),
        str(treasury.bid_percent),
        str(treasury.ask_percent),
        str(treasury.mid_percent),
        model.version,
        str(model.mean_bps),
        str(model.std_bps),
        str(model.observation_count),
        str(prior_state),
    )
    return hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()


def _blocked_result(
    *,
    maturity: str,
    snapshot_id: str,
    model: HistoricalModelState,
    prior_state: int,
    reasons: tuple[str, ...],
    mid_spread_bps: Decimal | None = None,
    spread_bid_side_bps: Decimal | None = None,
    spread_ask_side_bps: Decimal | None = None,
) -> LiveSignalResult:
    return LiveSignalResult(
        maturity=maturity,
        strategy_version=LIVE_SIGNAL_STRATEGY_VERSION,
        snapshot_id=snapshot_id,
        mid_spread_bps=mid_spread_bps,
        spread_bid_side_bps=spread_bid_side_bps,
        spread_ask_side_bps=spread_ask_side_bps,
        historical_mean_bps=model.mean_bps if _finite_decimal(model.mean_bps) else None,
        historical_std_bps=model.std_bps if _finite_decimal(model.std_bps) else None,
        z_score=None,
        prior_state=prior_state,
        state=0,
        blocked=True,
        reason_codes=reasons,
    )


def _transition(prior_state: int, z_score: Decimal) -> int:
    if prior_state not in {-1, 0, 1}:
        raise ValueError("prior_state must be -1, 0, or 1")

    long_entry = z_score <= -Z_ENTRY
    short_entry = z_score >= Z_ENTRY

    if prior_state == 0:
        if long_entry:
            return 1
        if short_entry:
            return -1
        return 0

    if prior_state == 1:
        if short_entry:
            return -1
        if z_score >= -Z_EXIT:
            return 0
        return 1

    if long_entry:
        return 1
    if z_score <= Z_EXIT:
        return 0
    return -1


def evaluate_live_signal(
    *,
    maturity: str,
    eris: ErisParRateQuote,
    treasury: TreasuryYieldQuote,
    model: HistoricalModelState,
    prior_state: int,
    now: datetime,
    max_quote_age_seconds: int,
) -> LiveSignalResult:
    if prior_state not in {-1, 0, 1}:
        raise ValueError("prior_state must be -1, 0, or 1")
    if not _aware(now):
        raise ValueError("now must be timezone-aware")
    if max_quote_age_seconds <= 0:
        raise ValueError("max_quote_age_seconds must be positive")

    reasons: list[str] = []

    if model.version != LIVE_SIGNAL_STRATEGY_VERSION:
        reasons.append("model_version_mismatch")
    if type(model.observation_count) is not int or model.observation_count < MIN_OBSERVATIONS:
        reasons.append("insufficient_history")
    if not _finite_decimal(model.mean_bps):
        reasons.append("invalid_historical_mean")
    if not _finite_decimal(model.std_bps) or model.std_bps <= 0:
        reasons.append("invalid_historical_std")

    quote_times = (eris.observed_at, treasury.observed_at)
    if any(not _aware(ts) for ts in quote_times):
        reasons.append("invalid_quote_timestamp")
    else:
        max_age = Decimal(max_quote_age_seconds)
        for ts in quote_times:
            age = Decimal(str((now - ts).total_seconds()))
            if age < 0:
                reasons.append("future_quote")
                break
            if age > max_age:
                reasons.append("stale_quote")
                break

    treasury_fields = (
        treasury.bid_percent,
        treasury.ask_percent,
        treasury.mid_percent,
    )
    if not all(_finite_decimal(v) and v > 0 for v in treasury_fields):
        reasons.append("invalid_treasury_quote")
    elif treasury.bid_percent > treasury.ask_percent:
        reasons.append("crossed_treasury_quote")
    elif treasury.mid_percent != (
        treasury.bid_percent + treasury.ask_percent
    ) / Decimal("2"):
        reasons.append("invalid_treasury_mid")

    eris_fields = (
        eris.bid_par_rate_bps,
        eris.ask_par_rate_bps,
        eris.mid_par_rate_bps,
    )
    if not all(_finite_decimal(v) for v in eris_fields):
        reasons.append("invalid_eris_rate")
    elif eris.ask_par_rate_bps > eris.bid_par_rate_bps:
        reasons.append("invalid_eris_rate_orientation")
    elif eris.mid_par_rate_bps != (
        eris.bid_par_rate_bps + eris.ask_par_rate_bps
    ) / Decimal("2"):
        reasons.append("invalid_eris_mid")

    snapshot_id = _snapshot_id(maturity, eris, treasury, model, prior_state)

    mid_spread = None
    lower_spread = None
    upper_spread = None
    if not any(
        reason in reasons
        for reason in (
            "invalid_treasury_quote",
            "crossed_treasury_quote",
            "invalid_treasury_mid",
            "invalid_eris_rate",
            "invalid_eris_rate_orientation",
            "invalid_eris_mid",
        )
    ):
        mid_spread = eris.mid_par_rate_bps - treasury.mid_bps
        # Conservative lower and upper executable-side spread bounds. ERIS
        # futures price and par swap rate move inversely; Treasury Yield
        # futures quote yield directly.
        lower_spread = eris.ask_par_rate_bps - treasury.ask_bps
        upper_spread = eris.bid_par_rate_bps - treasury.bid_bps

    if reasons:
        return _blocked_result(
            maturity=maturity,
            snapshot_id=snapshot_id,
            model=model,
            prior_state=prior_state,
            reasons=tuple(dict.fromkeys(reasons)),
            mid_spread_bps=mid_spread,
            spread_bid_side_bps=lower_spread,
            spread_ask_side_bps=upper_spread,
        )

    assert mid_spread is not None
    with localcontext() as context:
        context.prec = 50
        z_score = (mid_spread - model.mean_bps) / model.std_bps

    state = _transition(prior_state, z_score)
    return LiveSignalResult(
        maturity=maturity,
        strategy_version=LIVE_SIGNAL_STRATEGY_VERSION,
        snapshot_id=snapshot_id,
        mid_spread_bps=mid_spread,
        spread_bid_side_bps=lower_spread,
        spread_ask_side_bps=upper_spread,
        historical_mean_bps=model.mean_bps,
        historical_std_bps=model.std_bps,
        z_score=z_score,
        prior_state=prior_state,
        state=state,
        blocked=False,
        reason_codes=("within_signal_model",),
    )
