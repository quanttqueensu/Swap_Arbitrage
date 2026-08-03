"""Causal signal features."""

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal, localcontext

from .models import (
    NamedValue,
    PositionState,
    SignalDecision,
    SpreadObservation,
    TradeDirection,
)


def _finite_decimal(value: object) -> bool:
    return type(value) is Decimal and value.is_finite()


def _nonblank_text(value: object) -> bool:
    return type(value) is str and bool(value.strip())


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


def signal_transition(
    prior_state: PositionState,
    z_score: Decimal | None,
    traditional_net_bps: Decimal,
    reverse_net_bps: Decimal,
    data_ready: bool,
    risk_flatten: bool,
) -> tuple[PositionState, tuple[str, ...]] | None:
    """Return the next position state and its ordered action codes."""
    if (
        type(prior_state) is not PositionState
        or z_score is not None and not _finite_decimal(z_score)
        or not _finite_decimal(traditional_net_bps)
        or not _finite_decimal(reverse_net_bps)
        or type(data_ready) is not bool
        or type(risk_flatten) is not bool
    ):
        return None

    if risk_flatten:
        return (PositionState.FLAT, ("risk_flatten",)) if prior_state else (PositionState.FLAT, ())
    if not data_ready or z_score is None:
        return (PositionState.FLAT, ("data_flatten",)) if prior_state else (PositionState.FLAT, ())

    with localcontext() as context:
        context.prec = 50
        traditional_entry = z_score >= Decimal("2.0") and traditional_net_bps > 0
        reverse_entry = z_score <= Decimal("-2.0") and reverse_net_bps > 0
        if traditional_entry:
            return (
                (PositionState.TRADITIONAL, ("enter_traditional",))
                if prior_state is PositionState.FLAT
                else (PositionState.TRADITIONAL, ("exit_reverse", "enter_traditional"))
                if prior_state is PositionState.REVERSE
                else (prior_state, ())
            )
        if reverse_entry:
            return (
                (PositionState.REVERSE, ("enter_reverse",))
                if prior_state is PositionState.FLAT
                else (PositionState.REVERSE, ("exit_traditional", "enter_reverse"))
                if prior_state is PositionState.TRADITIONAL
                else (prior_state, ())
            )
        if prior_state is PositionState.TRADITIONAL and (
            z_score.copy_abs() <= Decimal("0.5") or traditional_net_bps <= 0
        ):
            return PositionState.FLAT, ("exit_traditional",)
        if prior_state is PositionState.REVERSE and (
            z_score.copy_abs() <= Decimal("0.5") or reverse_net_bps <= 0
        ):
            return PositionState.FLAT, ("exit_reverse",)
    return prior_state, ()


def _valid_observation(value: object) -> bool:
    if type(value) is not SpreadObservation:
        return False
    try:
        SpreadObservation(
            value.maturity,
            value.observation_time_utc,
            value.fixed_swap_spread_bps,
            value.expected_funding_spread_bps,
            value.gross_excess_spread_bps,
            value.traditional_cost_buffer_bps,
            value.reverse_cost_buffer_bps,
            value.traditional_net_opportunity_bps,
            value.reverse_net_opportunity_bps,
            value.z_score,
            value.observation_count,
            value.source_quality_ok,
            value.is_fresh,
        )
    except (TypeError, ValueError):
        return False
    return True


def _valid_prior(value: object) -> bool:
    return (
        not isinstance(value, str)
        and isinstance(value, Sequence)
        and all(type(item) is SpreadObservation for item in value)
    )


def generate_signal_decision(
    decision_id: str,
    observation: SpreadObservation,
    prior: object,
    prior_state: PositionState,
    risk_flatten: bool,
    strategy_version: str,
    configuration_version: str,
) -> SignalDecision | None:
    """Generate an immutable signal decision from one observed spread."""
    if (
        not _nonblank_text(decision_id)
        or not _valid_observation(observation)
        or type(prior_state) is not PositionState
        or type(risk_flatten) is not bool
        or not _nonblank_text(strategy_version)
        or not _nonblank_text(configuration_version)
    ):
        return None

    if risk_flatten:
        transition = signal_transition(
            prior_state, None, observation.traditional_net_opportunity_bps,
            observation.reverse_net_opportunity_bps, False, True,
        )
        if transition is None:
            return None
        new_state, actions = transition
        reason_code = actions[0] if actions else "risk_flatten_already_flat"
        features: tuple[NamedValue, ...] = ()
    else:
        if not _valid_prior(prior):
            return None
        z_score = causal_zscore(observation, prior)
        data_ready = (
            observation.source_quality_ok
            and observation.is_fresh
            and observation.observation_count == 252
            and z_score is not None
            and observation.z_score == z_score
        )
        transition = signal_transition(
            prior_state,
            z_score,
            observation.traditional_net_opportunity_bps,
            observation.reverse_net_opportunity_bps,
            data_ready,
            False,
        )
        if transition is None:
            return None
        new_state, actions = transition
        if actions:
            reason_code = "_then_".join(actions)
        elif new_state is PositionState.FLAT:
            reason_code = "data_unavailable" if not data_ready else "remain_flat"
        else:
            reason_code = "hold_traditional" if new_state is PositionState.TRADITIONAL else "hold_reverse"
        if data_ready:
            features = (
                NamedValue("z_score", z_score, "standard_deviations"),
                NamedValue("traditional_net_opportunity", observation.traditional_net_opportunity_bps, "bps"),
                NamedValue("reverse_net_opportunity", observation.reverse_net_opportunity_bps, "bps"),
            )
        else:
            features = (
                NamedValue("observation_count", Decimal(observation.observation_count), "observations"),
                NamedValue("gross_excess_spread", observation.gross_excess_spread_bps, "bps"),
                NamedValue("traditional_net_opportunity", observation.traditional_net_opportunity_bps, "bps"),
                NamedValue("reverse_net_opportunity", observation.reverse_net_opportunity_bps, "bps"),
            ) + ((NamedValue("z_score", z_score, "standard_deviations"),) if z_score is not None else ())

    return SignalDecision(
        decision_id=decision_id,
        maturity=observation.maturity,
        decision_time_utc=observation.observation_time_utc,
        prior_state=prior_state,
        new_state=new_state,
        direction=TradeDirection(new_state.value),
        reason_code=reason_code,
        feature_values=features,
        strategy_version=strategy_version,
        configuration_version=configuration_version,
    )
