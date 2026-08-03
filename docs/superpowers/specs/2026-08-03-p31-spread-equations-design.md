# P31 spread equations design

## Goal and approved input

Implement the MG2-approved P31 arithmetic as pure production functions without
changing any approved example. The production specification identifier is the
fixture's approved `schema_version`, `p10.strategy-equations.v1`, exposed as
`STRATEGY_SPEC_VERSION`.

The tracked golden artifact is
`docs/tests/fixtures/strategy_equation_examples.json`, moved there by the
approved repository reorganization. P31 reuses it byte-for-byte rather than
copying the playbook's obsolete `tests/fixtures/strategy_equation_cases.json`
path. Its starting SHA-256 is
`3fb9da5fc9ad255587ce93ea9770552f42566d56070f68ad1661709c030fbd76`.

The MG2 ledger and equation document say approved on 2026-07-31. The P11
source-coverage matrix named by the master plan is absent, and the equation
document still labels several production inputs unavailable pending P11. P31
therefore implements only explicit-input arithmetic, does not invent sources
or calendars, and does not claim the missing matrix exists. The user
authorized local gate approval with this limitation recorded.

## Ponytail design choice

Add only `strategy/spread.py`, update `strategy/__init__.py`, and add
`docs/tests/test_spread.py`. Use Python's `Decimal`, `localcontext`, rounding
constants, and `re`; add no dependency, service, base class, registry, builder,
configuration layer, or result model.

Two alternatives are rejected:

1. Domain result classes for every equation would duplicate the P30 boundary
   records and add types with one caller.
2. A generic configurable equation engine would turn frozen arithmetic into a
   framework and allow conventions P31 is forbidden to choose dynamically.

Plain tuples are sufficient for hedge quantities and basket legs. The
20 forecast steps in the approved funding estimator are identical, so their
mean is exactly the trailing mean; P31 calculates it once.

## Public functions

`strategy.spread` exposes:

```text
rate_decimal_to_bps(rate_decimal) -> Decimal | None
treasury_fractional_quote_to_points(whole_points, thirty_seconds,
                                    eighths_of_32nd) -> Decimal | None
tick_value_usd(minimum_increment_points, multiplier_usd_per_point)
    -> Decimal | None
fixed_swap_spread_bps(swap_rate_bps, treasury_rate_bps) -> Decimal | None
funding_spread_bps(floating_rate_bps, repo_rate_bps) -> Decimal | None
expected_funding_bps(consecutive_lagged_history_bps) -> Decimal | None
gross_excess_spread_bps(swap_spread_bps, expected_funding_bps)
    -> Decimal | None
directional_cost_buffer_bps(swap_bid_ask_usd, treasury_bid_ask_usd,
                            commission_exchange_usd, slippage_usd, roll_usd,
                            financing_not_in_funding_usd,
                            cost_base_dv01_usd_per_bp) -> Decimal | None
net_opportunity_bps(direction, gross_excess_bps, cost_buffer_bps)
    -> Decimal | None
dv01_hedge_quantities(direction, target_dv01_usd_per_bp,
                      swap_dv01_usd_per_bp,
                      treasury_dv01_usd_per_bp) -> tuple[int, int]
residual_dv01_usd_per_bp(swap_quantity, treasury_quantity,
                         swap_dv01_usd_per_bp,
                         treasury_dv01_usd_per_bp) -> Decimal | None
residual_fraction(net_dv01_usd_per_bp, target_dv01_usd_per_bp)
    -> Decimal | None
basket_pnl_usd(legs, total_cost_usd) -> Decimal | None
contract_turnover_contracts(quantities) -> int | None
```

`direction` is an exact non-flat `TradeDirection`. A basket leg is a tuple of
`(start_instrument_id, end_instrument_id, quantity_contracts,
multiplier_usd_per_point, start_price_points, end_price_points)`. Each endpoint
ID must be the same full quarterly `YIT`, `YIW`, `ZT`, or `ZF` contract. The
basket may contain different same-contract legs, which permits explicit old
and new roll intervals without cross-contract price changes.

## Validation and numerical rules

Functions accept exact finite `Decimal` values; they never coerce strings or
floats. Missing, nonfinite, or domain-invalid scalar inputs return `None`.
Costs and slippage are nonnegative. Multipliers, tick sizes, prices, and DV01
magnitudes are positive. Integer fields reject booleans.

The expected funding function consumes an already selected consecutive,
one-business-day-lagged history. Fewer than 40 observations returns `None`; at
most the last 60 are averaged. Calendar and publication selection remain an
upstream explicit-input responsibility because the approved production
calendar is unavailable; the function does not read the clock or infer a
Monday-Friday calendar.

DV01 hedge selection uses positive target and magnitude inputs, long-contract
exposure `-DV01`, half-ties-away swap rounding, only the Treasury floor and
adjacent ceiling, and the approved ordering `(absolute net DV01, gross DV01,
Treasury quantity)`. Invalid inputs or a zero rounded swap leg return `(0, 0)`.
Residual DV01 and the 5% allowance are calculated separately so no unrequested
risk-decision object is introduced.

Division uses a local precision of 50, matching the frozen fixture without
mutating the process-wide Decimal context. No tolerance is used for approved
literal values.

## Test and review boundary

Production-function tests load every P31-relevant frozen case: quote
conventions, three funding profiles, four directional economic examples, four
P&L/turnover examples, and seven hedge examples. Gross-history z-scores,
movement boundaries, and state examples remain P32 scope.

Systematic tests cover direction symmetry, decimal/basis-point conversion,
funding warm-up and 60-row bound, missing/nonfinite input, zero/negative DV01,
half rounding and hedge tie-breaking, residual boundaries, same-contract P&L,
roll turnover, reversal turnover, invalid contract identity, and unchanged
fixture bytes/hash. Review requires independent equation recalculation,
numerical rounding/tolerance inspection, and a Ponytail simplicity pass.
