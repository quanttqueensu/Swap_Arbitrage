# P10 Strategy Equations Design

**Status:** Approved in design review on 2026-07-29
**Prompt:** `P10 — Validate the economic and executable equations`
**Gate:** Stop at `MG2`; do not implement strategy behavior

## Purpose

P10 converts the swap-arbitrage hypothesis into a causal, unit-explicit
research contract that can be recalculated independently. It separates the
cash-market economic hypothesis from the futures basket used for paper
execution. It does not treat an Eris/Treasury futures-price difference as a
swap spread.

The approved executable universe is 2Y and 5Y. Generic economic equations may
refer to other maturities, but 10Y and 30Y executable mappings remain
unavailable until their contracts, data, and risk inputs are validated.

## Non-goals

P10 does not:

- implement production strategy functions or change existing proxy behavior;
- access Quantt, Cloudflare, IBKR, or public market-data APIs;
- submit, cancel, or simulate an external broker order;
- choose live execution costs without validated inputs;
- approve 10Y or 30Y executable baskets;
- create the P11 data-source coverage matrix; or
- proceed beyond `MG2`.

## Deliverables

P10 produces:

1. `docs/research/strategy-equations.md`, the normative research contract;
2. `tests/fixtures/strategy_equation_examples.json`, containing synthetic
   inputs and expected results; and
3. `tests/test_strategy_equation_examples.py`, which independently
   recalculates the examples using `Decimal`.

No production strategy module is created in P10.

## Contract layers

### Economic hypothesis

The economic layer operates on maturity-matched rates in basis points:

\[
SS_{m,t}=CMS_{m,t}-CMT_{m,t}
\]

\[
FS_t=L_t-repo_t
\]

\[
X^{gross}_{m,t}=SS_{m,t}-\widehat{E}_t[\overline{FS}_m]
\]

An exact result requires exact maturity-matched swap, Treasury, floating-rate,
and collateral-consistent repo inputs. Substitutes remain explicitly named
proxies.

### Executable futures basket

The executable layer converts an approved economic direction into Eris and
Treasury futures quantities using current contract metadata and DV01. It does
not derive the economic signal from incompatible futures price levels.

### Availability and proxy boundary

Every input and output is classified as exact, proxy, assumed, derived, or
unavailable. Missing exact inputs block an exact result. A proxy result cannot
be relabelled as an economic swap spread or complete-strategy result.

## Approved economic parameters

### Funding expectation

The baseline estimator is a causal trailing simple mean:

- 60 completed business-day observations;
- minimum 40 observations;
- one-business-day lag;
- a flat forecast over a 20-business-day expected holding horizon; and
- uniform forecast-horizon weights.

Let \(N_t=\min(60,n_t)\), where \(n_t\) is the number of consecutive,
available lagged observations and \(N_t\ge40\) is required. For every forecast
step \(h\in\{1,\ldots,20\}\):

\[
\widehat{FS}_{t,h}=
\frac{1}{N_t}\sum_{j=1}^{N_t}FS_{t-j}
\]

and:

\[
\widehat{E}_t[\overline{FS}_m]
=\frac{1}{20}\sum_{h=1}^{20}\widehat{FS}_{t,h}
\]

The first forecast is unavailable until 40 consecutive lagged observations
exist. With 40–59 observations, the denominator is the exact available count.
Once 60 observations exist, the window remains fixed at 60. A missing date
breaks consecutiveness rather than being forward-filled.

The estimator consumes exact \(L-repo\) observations when available. The
current `EFFR-SOFR` calculation is a labelled proxy, not an exact substitute.
Forward-curve estimation remains unavailable until P11 validates the required
fields.

### Directional costs and eligibility

Each round-trip cost component remains an explicit USD input:

- swap-futures bid/ask;
- Treasury-futures bid/ask;
- commissions and exchange fees;
- slippage;
- close/open roll costs; and
- financing not already represented in the expected funding term.

The components are divided by absolute target swap-leg DV01 to convert USD to
basis points. The same financing burden cannot appear in both expected funding
and execution costs.

\[
TC^d_{m,t}=
\frac{\sum C^d_{m,t,\mathrm{component}}}
{|DV01^{target}_{m,t}|}
\]

\[
X^{net,d}_{m,t}=dX^{gross}_{m,t}-TC^d_{m,t}
\]

The additional entry buffer is \(B^{entry}_m=0\) bps for 2Y and 5Y. Net
opportunity must be strictly positive. Numerical example costs are synthetic
and are not live or backtest assumptions.

### Causal z-score and state thresholds

\[
z_{m,t}=
\frac{X^{gross}_{m,t}-\mu_{m,t^-}}
{\sigma_{m,t^-}}
\]

The baseline uses:

- the previous 252 completed business-day observations;
- sample standard deviation;
- exclusion of the current observation;
- all 252 observations before the first valid z-score;
- traditional entry at \(z\ge 2.0\);
- reverse entry at \(z\le -2.0\); and
- exit hysteresis at \(|z|\le 0.5\).

An open position also exits when its directional net opportunity is
nonpositive, required data is stale or missing, or risk requests flattening.
A reversal is an exit followed by a separately charged new entry.

## Decision clock and Agent 2 movement trigger

The decision interval is one synchronized completed business day:

\[
\Delta SS_{m,t}=SS_{m,t}-SS_{m,t-1}
\]

- \(\Delta SS\ge 5.00\) bps qualifies for the traditional direction.
- \(\Delta SS\le -5.00\) bps qualifies for the reverse direction.
- Values between -5.00 and +5.00 bps do not qualify.

The decision timestamp is the first UTC instant at which all required
same-date observations are published and available. No guessed fixed clock,
forward fill, or intraday approximation is permitted. Delayed publication
delays the decision. Intraday triggers remain unavailable until validated
quote capture exists.

## Executable direction and DV01 design

The source-verified direction table must establish:

| Economic direction | Eris side | Swap exposure | Treasury side |
|---|---:|---|---:|
| Traditional | Buy | Receive fixed, pay compounded SOFR | Sell |
| Reverse | Sell | Pay fixed, receive compounded SOFR | Buy |

One long Eris or Treasury contract has negative signed exposure to a
one-basis-point rate increase because its price falls as rates rise. Absolute
contract DV01 may be used for sizing only after the side table is verified.

CME's published Eris/Treasury spread ratios are reasonableness checks. Current
contract and CTD DV01 remain authoritative for hedge sizing. If primary
sources disagree about a published ratio, the ratio is marked unresolved
rather than selected by inference.

For positive target DV01 magnitude \(T\):

1. Round the swap-leg magnitude nearest to \(T/D_S\), with half ties away from
   zero, and apply the economic-direction sign.
2. Calculate the continuous Treasury hedge.
3. Evaluate the adjacent floor and ceiling integer Treasury quantities.
4. Choose the candidate with lowest absolute residual net DV01; a tie chooses
   lower gross DV01.
5. Block the basket if either leg is unavailable or
   \(|DV01^{net}|>0.05T\).

If the nearest swap-leg magnitude is zero, the target is below the executable
minimum and the basket is blocked; the algorithm does not force a one-contract
minimum or create a one-leg hedge.

P&L uses each contract's official price multiplier and same-contract price
change. A roll closes the old contract and opens the new contract as two
charged transactions. Cross-contract price changes are never treated as
same-contract return.

## Golden examples and tests

The fixture contains synthetic, unit-labelled inputs for:

- two traditional examples;
- two reverse examples;
- entry and position persistence;
- hysteretic exit;
- reversal with exit and re-entry costs;
- close/open roll accounting; and
- risk flattening.

The tests independently recalculate:

- rate and spread conversions;
- funding expectation and warm-up;
- gross and net opportunity;
- 4.99, 5.00, and 5.01 bp movement boundaries in both directions;
- z-score entry and exit boundaries;
- zero variance and insufficient history;
- contract P&L signs and multipliers;
- integer DV01 hedge rounding and tie-breaking;
- the 5% residual-DV01 boundary; and
- close/open turnover and costs for reversal and roll.

The fixture is data, not executable strategy logic. Test helpers use `Decimal`
to avoid binary floating-point ambiguity in hand-worked examples.

## Fail-closed behavior

No exact signal or basket is produced when any of these conditions holds:

- missing, stale, wrong-unit, or maturity-mismatched economic inputs;
- an observation timestamp after the decision timestamp;
- insufficient estimator or z-score history;
- zero or non-finite z-score variance;
- missing, stale, or nonpositive contract price or DV01;
- unavailable contract multiplier or quote convention;
- residual net DV01 above the approved limit;
- a one-leg basket;
- a source conflict that affects sign, unit, or multiplier; or
- risk requires blocking or flattening.

Proxy inputs produce proxy-labelled outputs only. Tests perturb future
observations to prove historical decisions are unchanged.

## Primary-source hierarchy

P10 records an access date and direct official URL for each convention. The
initial sources below were accessed on 2026-07-29:

- CME Group Eris SOFR Swap Futures FAQ and overview for contract structure,
  long/short swap exposure, notional, accrual, and price construction:
  <https://www.cmegroup.com/markets/interest-rates/files/eris-sofr-swap-futures-faq.pdf>
  and
  <https://www.cmegroup.com/markets/interest-rates/files/eris-sofr-swap-futures-overview.pdf>;
- CME Group Eris/Treasury spread primer for economic direction and published
  spread ratios:
  <https://www.cmegroup.com/articles/2024/trading-swap-spreads-with-futures-a-primer-for-eristreasury-swap-spreads.html>;
- CME Group 2-Year and 5-Year Treasury futures specifications and delivery
  guide for contract units, quote conventions, ticks, deliverable baskets,
  conversion factors, and CTD behavior:
  <https://www.cmegroup.com/markets/interest-rates/us-treasury/2-year-us-treasury-note.contractSpecs.html>,
  <https://www.cmegroup.com/markets/interest-rates/us-treasury/5-year-us-treasury-note.contractSpecs.html>,
  and
  <https://www.cmegroup.com/content/dam/cmegroup/trading/interest-rates/files/us-treasury-futures-delivery-process.pdf>;
- U.S. Treasury methodology and FAQ for CMT meaning, observation timing, and
  compounding convention:
  <https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology>
  and
  <https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/interest-rates-frequently-asked-questions>;
- Federal Reserve Bank of New York reference-rate pages for SOFR, EFFR, and
  Treasury-repo reference-rate definitions:
  <https://www.newyorkfed.org/markets/reference-rates>.

Marketing summaries may locate a primary specification, but they cannot
override a rulebook or contract specification. A material source conflict is
recorded as unresolved and blocks the affected example.

## Deliberately unavailable items

The following are unavailable rather than silently assumed:

- exact maturity/collateral-specific repo history until P11 validates a source;
- exact historical maturity-matched SOFR swap rates until P11 validates a
  source;
- observed historical bid/ask, commissions, slippage, financing, and roll
  costs until their sources are approved;
- executable 10Y and 30Y contract mappings;
- an intraday decision interval; and
- a market-implied forward funding estimator.

## Review and acceptance

Before MG2:

1. a financial-equations reviewer independently recalculates every fixture;
2. a causality reviewer verifies observation and publication lags;
3. a market-units reviewer checks contract signs, multipliers, ticks, and DV01;
4. focused and full tests pass;
5. source conflicts and unavailable inputs remain explicit; and
6. the user manually approves equations, signs, units, estimator, examples,
   and proxy boundaries.

P10 then stops at MG2. P11 starts only after the P10 equation contract is
approved.
