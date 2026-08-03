# P32 Causal Signal Generation Design

## Scope and decision

P32 adds one standard-library-only module, `strategy/signal_generation.py`,
plus focused tests. It implements the MG2-approved causal z-score and state
rules over immutable P30 `SpreadObservation` values and returns P30
`SignalDecision` records. It does not modify `signal_pipeline.py`; that pandas
price-residual pipeline remains the labelled legacy proxy described by the
master plan.

The selected approach is direct functions. A state-machine class would add
mutable lifecycle and a strategy engine would duplicate later P36 work.
Extending the pandas proxy would mix the new excess-spread strategy with a
different legacy signal. Ponytail therefore selects one pure module with no
new dependency, service, registry, or configuration layer.

P31 passed MG5 equation comparison at commit `9d92519`. P32 works directly on
`main`, with self-approved design checkpoints by user instruction.

## Public functions

```python
causal_zscore(
    current: SpreadObservation,
    prior: object,
) -> Decimal | None

signal_transition(
    prior_state: PositionState,
    z_score: Decimal | None,
    traditional_net_bps: Decimal,
    reverse_net_bps: Decimal,
    data_ready: bool,
    risk_flatten: bool,
) -> tuple[PositionState, tuple[str, ...]] | None

generate_signal_decision(
    decision_id: str,
    observation: SpreadObservation,
    prior: object,
    prior_state: PositionState,
    risk_flatten: bool,
    strategy_version: str,
    configuration_version: str,
) -> SignalDecision | None

rank_opportunities(observations: object) -> tuple[str, ...] | None
```

`causal_zscore` accepts exactly 252 prior `SpreadObservation` instances of the
same maturity, in strictly increasing UTC observation time, all strictly
before `current`. Duplicate, reversed, current, or future timestamps block the
result. Historical source quality must be true. It calculates the mean and
sample standard deviation from prior `gross_excess_spread_bps` values only;
the current value is excluded. Zero variance or any invalid collection returns
`None`. All Decimal arithmetic uses a local precision of 50 and leaves the
caller context unchanged.

The available P30 model has observation timestamps but no publication timestamp
or approved business-day calendar. The function can therefore enforce ordering
and exclusion, but not the P10 exact-consecutive-business-date or revision
cutoff rules. This limitation remains explicit and must not be presented as
production calendar validation.

## State and decisions

`signal_transition` implements the eight frozen state examples and exact
boundaries:

- traditional entry requires `z >= 2.0` and traditional net strictly positive;
- reverse entry requires `z <= -2.0` and reverse net strictly positive;
- an opposite eligible entry exits first and enters second;
- an existing side exits when `abs(z) <= 0.5` or its net is nonpositive;
- risk flatten has first precedence, then missing/stale-data flatten;
- otherwise the state persists.

Inputs use exact `PositionState`, finite exact Decimals, and exact bools.
Malformed inputs return `None`. A missing z-score makes data unavailable.

`generate_signal_decision` derives the causal z-score, requires current source
quality, freshness, and `observation_count == 252`, then calls the transition.
If the observation supplies a non-null z-score, it must equal the recomputed
value; a mismatch is treated as unavailable data. The decision direction is
the new position direction. Its reason code is the one action, the ordered
reversal joined with `_then_`, or `remain_flat`/`hold_traditional`/
`hold_reverse`. Feature values contain the calculated z-score and directional
net opportunities for normal decisions, in that order. Data-unavailable
decisions contain `observation_count`, gross excess, and both directional nets,
plus z-score only when it was calculated. Risk-flatten decisions have no
economic feature values because risk precedence bypasses them. Malformed exact
types return `None`; valid but unavailable market data produces the specified
flat/hold decision rather than an exception.

The function uses only its arguments and the observation timestamp. It has no
file access, implicit clock, calendar inference, order creation, broker, or
network behavior.

## Cross-maturity ranking

`rank_opportunities` accepts a synchronized sequence of unique-maturity
`SpreadObservation` values. Malformed collections, duplicate maturities, or
different decision timestamps return `None`. Stale, poor-quality, missing-z,
or insufficient-observation rows are excluded. Eligible rows use the same
strict-positive directional-net and inclusive entry thresholds as the state
machine. Results are maturity names ordered by descending `abs(z_score)`, then
ascending maturity text.

P10 required a ranking rule but its approved artifacts contain no ranking score
or tie-break. P32 explicitly chooses the smallest rule consistent with the
existing labelled proxy experiment—absolute z-score—and a deterministic text
tie-break. This is a documented P32 convention, not a claim that P10 froze it;
any later MG2 correction must version and retest the rule.

## Tests and evidence

`docs/tests/test_signal_generation.py` will reuse the frozen
`strategy_equation_examples.json` bytes and hash. Tests are written before
production code and cover:

- every gross-history profile, 251/252/253 warm-up, sample mean/std, zero
  variance, current exclusion, caller Decimal context, timestamp order,
  maturity/source-quality rejection, and future perturbation;
- all eight frozen state examples, entry/exit boundaries, persistence,
  opposite-direction reversal ordering, missing/stale input, and flatten
  precedence;
- immutable `SignalDecision` fields, reason codes, exact feature values,
  declared-z mismatch, strategy/configuration versions, and no implicit time;
- synchronized cross-maturity eligibility, absolute-z ordering, text tie-break,
  stale/missing exclusions, duplicate maturities, and timestamp mismatch;
- a compact fixture-backed decision trace in `docs/verification/P32.md`.

Requirements and lookahead reviewers use Terra high when Luna is unavailable;
no Sol reviewer is used. P32 stops at the MG5 evidence boundary before P33.
