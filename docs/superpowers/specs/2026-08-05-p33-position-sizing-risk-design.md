# P33 Position Sizing and Risk Signals Design

## Goal

Implement causal, broker-independent position sizing and risk decisions for the
shared strategy core. P33 converts an approved signal into an immutable
`TargetPosition`, scales capacity limits instead of rejecting otherwise valid
risk, and blocks every non-capacity safety failure through an immutable
`RiskDecision`.

## Scope and boundaries

The frozen sizing/risk configuration version is
`p33.position-sizing-risk.v1`. P33 does not reinterpret the existing
`p10.strategy-equations.v1` economic strategy version.

P33 adds only `strategy/position_sizing.py`, `strategy/risk_signals.py`, their
focused tests, public exports, and a verification record. It reuses the P30
models and P31 integer hedge equations. It does not change `MarketSnapshot`,
canonical schemas, the legacy pandas `risk_pipeline.py`, configuration files,
cost models, portfolio ranking, backtests, files, clocks, brokers, or orders.

All public functions accept exact built-in values, fail closed with `None` or
a blocked `RiskDecision`, use `Decimal` arithmetic under a local precision-50
context, and leave the caller's Decimal context unchanged. Sequences exclude
strings and require exact member types. Booleans are never accepted as
integers.

## Scaling formulas

### Volatility scale

`volatility_scale(current_realized_vol, prior_realized_vols)` requires a
positive finite current volatility and exactly 63 positive finite prior
realized-volatility observations in causal order. The reference volatility is
the median of the 63 prior observations, so no interpolation convention is
needed. The scale is:

```text
min(1, prior_median_volatility / current_realized_volatility)
```

Missing, malformed, nonpositive, short, or long history returns `None`. Low
volatility never increases risk above the base target.

### Signal-strength scale

`signal_strength_scale(z_score)` requires a finite Decimal z-score. The scale
is:

```text
min(1, abs(z_score) / 2)
```

This preserves the existing risk behavior: the inclusive entry threshold at
absolute z-score 2 receives full strength, while a held position scales down
as it approaches the exit region. A missing or malformed z-score returns
`None`.

### Liquidity scale

`liquidity_scale(swap_quantity, treasury_quantity,
swap_available_contracts, treasury_available_contracts)` requires nonzero
exact integer provisional quantities and nonnegative exact integer available
sizes. The scale is:

```text
min(1,
    swap_available_contracts / abs(swap_quantity),
    treasury_available_contracts / abs(treasury_quantity))
```

Zero displayed size produces scale zero. Missing or malformed values return
`None`. The sizes are explicit causal inputs; P33 does not infer them from
prices or add them to `MarketSnapshot`.

### Target DV01

`scaled_target_dv01(base_target, volatility, strength, liquidity)` requires a
positive finite base target and three finite Decimal scales in `[0, 1]`:

```text
base_target * volatility * strength * liquidity
```

The result is in USD per basis point. Any invalid input returns `None`.

### Hand-worked scale examples

- Sixty-three prior volatilities with median `0.8` and current volatility `1`
  produce volatility scale `0.8`; current volatility `0.5` produces `1`.
- Z-scores `0`, `1`, `2`, and `-3` produce strength scales `0`, `0.5`, `1`,
  and `1`.
- Provisional quantities `10` and `-4` with displayed sizes `5` and `4`
  produce liquidity scale `min(1, 5/10, 4/4) = 0.5`.
- Base target `3000` with volatility `0.8`, strength `0.5`, and liquidity
  `0.5` produces final pre-cap target `600` USD per basis point.

## Target-position construction

`build_target_position(...)` is a keyword-only pure orchestrator. It consumes
the maturity and instrument identities, non-flat `TradeDirection`, base target,
current and prior volatility inputs, z-score, displayed leg sizes, positive leg
DV01 magnitudes, current signed quantities, contract caps, portfolio gross-DV01
capacity, and an explicit nonnegative expected cost.

Construction proceeds in this order:

1. Calculate volatility and strength scales.
2. Calculate the pre-liquidity target and call the P31
   `dv01_hedge_quantities` selector.
3. Calculate the liquidity scale from the provisional integer basket.
4. Recalculate the target and integer basket after liquidity scaling.
5. Calculate one capacity scale as the minimum of 1 and every applicable
   swap-contract, Treasury-contract, and available portfolio gross-DV01 ratio.
   A contract cap of zero retains the repository's existing convention of no
   configured cap.
6. Recalculate the target and integer basket once after capacity scaling.
7. Verify both legs are nonzero, all configured caps are satisfied, gross
   DV01 is within available capacity, and residual DV01 is at most 5% of the
   final target, inclusive.

For example, provisional quantities `10` and `-4`, provisional gross DV01
`4800`, a swap cap of `5`, no Treasury cap, and available portfolio gross
capacity `3000` produce capacity scale `min(1, 5/10, 3000/4800) = 0.5` before
the final integer basket is recalculated.

Capacity constraints reduce the target; they do not independently block it.
If integer rounding leaves no nonzero two-leg basket within all limits, the
orchestrator returns `None` because zero risk is the only valid scaled result.
The final `TargetPosition` records signed quantities, final target, gross and
residual DV01, turnover from current to target quantities, the caller-supplied
expected cost, and stable diagnostics naming the scale and cap outcome.

P33 does not estimate costs. P34 will replace the explicit expected-cost input
with the shared cost-model output without changing the position-sizing
equations.

## Risk decisions

`evaluate_risk(...)` is a keyword-only pure function using explicit booleans,
integer counters, and Decimal limits and measurements. It does not inspect
files, wall-clock time, broker objects, or environment state.

Capacity-only inputs use the scale calculated by position sizing. Every other
failure blocks new risk:

- stale or missing market data;
- invalid, locked, or crossed bid/ask data;
- missing or nonpositive price or DV01;
- residual or portfolio net-DV01 breach;
- order-rate or working-order breach;
- maximum paper-session loss or drawdown breach;
- margin-reserve failure;
- broker disconnection or reconciliation mismatch;
- contract-expiry or roll restriction; and
- scheduled or emergency flattening.

Precedence is deterministic:

1. emergency flatten;
2. scheduled flatten;
3. data and market validity;
4. connection, reconciliation, and roll state;
5. session loss, drawdown, and margin;
6. residual/net DV01 and order limits;
7. allowed, possibly capacity-scaled risk.

All applicable reason codes are retained once precedence reaches their class,
in declaration order without duplicates. An emergency or scheduled flatten
uses only its controlling flatten reason. A blocked decision has scale zero.
An allowed decision has the supplied capacity scale. When a hard failure
occurs with existing exposure, `flatten_requested` is true and urgency is
`EMERGENCY`; without exposure it blocks entry without a redundant flatten.
Scheduled flatten uses `SCHEDULED`. Emergency flatten uses `EMERGENCY`.

A valid state with capacity scale `0.5` returns `allowed=True`, `scale=0.5`,
and no flatten request. The same state with stale data returns `allowed=False`,
`scale=0`, and `stale_market_data`; if exposure exists it also requests an
emergency flatten. Explicit scheduled flatten returns only
`scheduled_flatten` with scheduled urgency, while explicit emergency flatten
returns only `emergency_flatten` with emergency urgency.

Every `RiskDecision` includes stable `NamedValue` tuples for the numeric limits
and measured values actually used. This preserves auditability without adding
new configuration dataclasses.

## Validation and testing

Focused `unittest` modules cover:

- scale values at 0, interior values, and 1;
- exact 63-observation volatility warm-up and future-row exclusion;
- malformed, nonfinite, nonpositive, and wrong-type inputs;
- both trade directions and integer hedge rounding;
- inclusive 5% residual tolerance and just-over-boundary rejection;
- proportional swap, Treasury, and gross-DV01 capacity scaling;
- monotonicity: tighter capacity never increases final risk;
- zero-capacity and zero-liquidity behavior;
- turnover from current to target positions;
- every risk reason, precedence, flatten urgency, and stable evidence values;
- preservation of the complete caller Decimal context; and
- absence of file, clock, broker, IBKR, pandas, and network imports.

Final verification runs the focused P33 tests, all `docs/tests`, Agent 0 tests,
`compileall`, the existing self-checks, and `git diff --check`. P33 stops at
MG5 with boundary-value evidence and hand-checked hedge examples; it does not
approve MG5 or begin P34.
