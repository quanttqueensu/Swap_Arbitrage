# Swap Arbitrage Project Contracts

This document defines the non-negotiable vocabulary, equations, interfaces,
file responsibilities, data schemas, and invariants used by every phase in
`MASTER_PLAN.md`.

## Vocabulary and scope

- **Paper-only:** Every broker connection and order is restricted to an IBKR
  paper account. Real-money trading is not a supported mode or future goal.
- **Live data:** Data observed at the current time. It does not imply
  live-capital trading.
- **Agent:** An incremental IBKR paper experiment. Agent 0 is random; each later
  agent adds one approved behavior.
- **Complete strategy:** The integrated strategy defined by the approved
  hypothesis equations, costs, sizing, risk, and portfolio rules for the
  explicitly approved maturity universe. A 2Y/5Y run is labelled
  `complete_2y_5y`; only a validated 2Y/5Y/10Y/30Y run is a complete
  four-maturity strategy.
- **Naive complete strategy:** The same complete strategy evaluated with fixed,
  declared bid/ask, commission, slippage, funding, and roll assumptions.
- **Realistic complete strategy:** The same complete strategy evaluated with
  time-varying observed inputs when available and conservative, labelled
  fallbacks otherwise.
- **Proxy:** A measurable substitute that does not implement the economic
  hypothesis exactly. Proxy results must retain the word “proxy.”
- **Decision timestamp:** The instant at which all features used by a decision
  are observable. An order cannot fill before this timestamp.

## Units and sign conventions

Internal names must carry units when ambiguity is possible:

- Rates and spreads: basis points, using suffix `_bps`.
- Raw rate decimals: suffix `_decimal`; convert once at an input boundary.
- Prices: exchange or source price points, suffix `_price`.
- DV01: US dollars per one basis-point parallel rate increase, suffix
  `_dv01_usd_per_bp`.
- P&L and costs: US dollars, suffix `_usd`.
- Times: UTC ISO 8601 timestamps, suffix `_utc`; research daily observations use
  ISO date.
- Quantities: signed integer contracts. Positive means long the exchange
  contract; negative means short.

Rate sensitivity signs must be represented explicitly. For a position with
signed contract quantity \(q_i\), price multiplier \(M_i\), and a quoted price
change \(\Delta P_i\):

\[
\mathrm{PnL}_{i,t} = q_{i,t-1} M_i \Delta P_{i,t} - C_{i,t}
\]

Instrument documentation and two hand-calculated examples must establish how a
long contract responds to a one-basis-point rate increase. Code may use
absolute exchange DV01 for sizing only when leg direction is separately
explicit and the golden sign tests pass.

## Economic hypothesis equations

For maturity \(m\) and decision time \(t\):

### Fixed swap spread

\[
SS_{m,t} = R^{swap}_{m,t} - Y^{Treasury}_{m,t}
\]

where both rates are maturity-matched and expressed in basis points.

### Floating funding spread

\[
FS_t = L_t - r^{repo}_t
\]

where \(L_t\) is the approved floating reference rate and \(r^{repo}_t\) is the
approved maturity/collateral-consistent repo rate, both in basis points. Using
EFFR minus SOFR is a labelled proxy, not an exact implementation of this
equation.

### Expected funding burden

For an approved horizon of \(H_m\) observations:

\[
\widehat{E}_t[\overline{FS}_{m}] =
\sum_{h=1}^{H_m} w_{m,h}\widehat{FS}_{t,h},
\qquad
\sum_{h=1}^{H_m} w_{m,h}=1
\]

The Phase 1 specification chooses and freezes the causal estimator and weights.
It must use only information available by \(t\). A historical rolling mean is
acceptable only when explicitly labelled as that estimator.

### Gross excess spread

\[
X^{gross}_{m,t} = SS_{m,t} -
\widehat{E}_t[\overline{FS}_{m}]
\]

Positive gross excess favors the traditional receive-fixed/short-Treasury
direction. Negative gross excess favors the reverse direction.

### Directional round-trip cost buffer

All observed or assumed execution costs are converted to basis points of
swap-leg DV01:

\[
TC^{d}_{m,t} =
\frac{
C^{swap,d}_{m,t}
+ C^{Treasury,d}_{m,t}
+ C^{commission}_{m,t}
+ C^{slippage}_{m,t}
+ C^{roll}_{m,t}
+ C^{financing}_{m,t}
}{
|DV01^{target}_{m,t}|
}
\]

where \(d \in \{-1,+1\}\) is trade direction and numerator terms are US
dollars. Because DV01 is dollars per basis point, the quotient is basis points.
Naive mode uses frozen constants. Realistic mode uses observed bid/ask and
approved fee/funding inputs. \(C^{financing}\) includes only carrying costs not
already represented in the expected floating funding spread; the same cost
cannot appear in both terms.

### Net directional opportunity

\[
X^{net,d}_{m,t} = d \cdot X^{gross}_{m,t} - TC^{d}_{m,t}
\]

Direction \(d=+1\) represents the traditional trade and \(d=-1\) the reverse.
A direction is economically eligible only when
\(X^{net,d}_{m,t} > B^{entry}_{m}\), where \(B^{entry}_{m}\) is an approved
additional safety buffer in basis points.

### Causal standardized dislocation

\[
z_{m,t} =
\frac{X^{gross}_{m,t}-\mu_{m,t^-}}
{\sigma_{m,t^-}}
\]

\(\mu_{m,t^-}\) and \(\sigma_{m,t^-}\) use only observations available before
the decision observation. If an end-of-day value is included, the earliest
permitted decision is after that close. Zero variance or insufficient history
produces no signal.

### State transition

For position state \(p_{m,t}\in\{-1,0,+1\}\):

- Enter \(+1\) only when the positive direction is economically eligible and
  \(z_{m,t}\ge z^{entry}_m\).
- Enter \(-1\) only when the negative direction is economically eligible and
  \(z_{m,t}\le -z^{entry}_m\).
- Exit \(+1\) when its net opportunity is nonpositive, its exit threshold is
  crossed, a risk signal requires flattening, or required data becomes stale.
- Exit \(-1\) under the symmetric condition.
- A direct reversal is represented as an exit followed by a new entry, so
  turnover and costs charge both actions.

The inequalities above are the economic signal convention. Implementation
cannot proceed until the economic direction and each quoted instrument
direction agree in golden tests.

## Executable futures basket equations

The economic signal and the executable hedge are separate objects. Do not
subtract an Eris futures price directly from a Treasury futures price.

Let \(D_i>0\) be the absolute contract DV01 and
\(s_i\in\{-1,+1\}\) be the documented rate-sensitivity sign of one long
contract. The signed one-contract exposure to a one-basis-point rate increase
is \(\delta_i=s_iD_i\). Let \(a_S(d)\in\{-1,+1\}\) be the exchange-contract
side that implements economic direction \(d\), fixed by the approved quote
convention table.

\[
q_S = a_S(d) \cdot
\operatorname{round}\left(
\frac{DV01^{target}}{|\delta_S|}
\right)
\]

\[
q_T =
\operatorname{round}\left(
-\frac{q_S \delta_S}{\delta_T}
\right)
\]

\[
DV01^{net} = q_S \delta_S + q_T \delta_T
\]

\[
DV01^{gross} = |q_S \delta_S| + |q_T \delta_T|
\]

The rounding policy chooses the integer pair that minimizes
\(|DV01^{net}|\) while respecting approved contract, gross-DV01, liquidity, and
margin limits. A trade is blocked when residual net DV01 exceeds its approved
limit.

Basket mark-to-market is:

\[
\mathrm{PnL}^{basket}_t =
q_{S,t-1}M_S\Delta P_{S,t}
+q_{T,t-1}M_T\Delta P_{T,t}
-C_t
\]

On a contract roll, close and open turnover are both charged. Cross-contract
price changes are never treated as same-contract P&L.

## Position sizing and risk equations

Base target DV01 is scaled only by approved, causal terms:

\[
DV01^{target}_{m,t} =
DV01^{base}_m
\cdot s^{vol}_{m,t}
\cdot s^{strength}_{m,t}
\cdot s^{liquidity}_{m,t}
\]

Each scale lies in \([0,1]\). It must have a named formula, an approved
lookback, causal timestamps, and unit tests at 0, interior values, and 1.

Portfolio controls calculate:

\[
DV01^{gross}_{portfolio,t}
=\sum_m (|q_{S,m,t}\delta_{S,m,t}|+|q_{T,m,t}\delta_{T,m,t}|)
\]

\[
DV01^{net}_{portfolio,t}
=\sum_m (q_{S,m,t}\delta_{S,m,t}+q_{T,m,t}\delta_{T,m,t})
\]

`risk_signals.py` returns a decision with `allowed`, `scale`, and explicit
reason codes. At minimum, paper execution must cover:

- stale or missing market data;
- invalid bid/ask or locked/crossed data;
- missing or nonpositive price/DV01;
- contract or gross/net DV01 limits;
- order-rate and working-order limits;
- maximum paper session loss and drawdown;
- margin-reserve failure;
- broker disconnection or reconciliation mismatch;
- contract-expiry/roll restrictions;
- scheduled and emergency flattening.

No risk function may submit an order. It produces a decision consumed by an
adapter.

## Shared strategy interfaces

The Phase 4 implementation may use frozen dataclasses or equivalent immutable
records with these semantic contracts:

### `MarketSnapshot`

Consumes one causal view of:

- `decision_time_utc`;
- rates by series and maturity;
- executable quotes or approved settlements by instrument;
- contract metadata and DV01;
- data source and observation timestamps;
- paper positions and working orders when risk evaluation needs them.

### `SpreadObservation`

Produces, per maturity:

- fixed swap spread, expected funding spread, and gross excess spread in bps;
- directional cost buffers and net opportunities in bps;
- z-score and observation count;
- source quality and freshness flags.

### `SignalDecision`

Produces:

- maturity and decision timestamp;
- prior and new state;
- direction and reason code;
- feature values actually used;
- strategy and configuration versions.

### `TargetPosition`

Produces:

- signed swap and Treasury quantities;
- target, gross, and residual net DV01;
- expected turnover and cost;
- rounding and cap diagnostics.

### `RiskDecision`

Produces:

- `allowed`;
- scale in `[0,1]`;
- immutable reason-code list;
- flatten request and urgency;
- limits and measured values used by the decision.

### `OrderIntent`

Produces an execution request without importing IBKR:

- run, agent, strategy, and decision IDs;
- instrument ID, side, quantity, order type, and time in force;
- earliest submission/activation and expiry timestamps;
- reference price and maximum permitted slippage;
- paper-only marker that must be true.

The backtester converts intents into simulated fills. The IBKR adapter converts
them into paper orders. Strategy modules do not know which adapter is active.

## File responsibility contracts

- `strategy/models.py`: immutable records and enums only.
- `strategy/spread.py`: equations and unit conversion; no rolling state, files,
  broker calls, or P&L accounting.
- `strategy/signal_generation.py`: causal rolling features and state
  transitions; no order creation.
- `strategy/position_sizing.py`: integer hedge selection and target scaling; no
  broker calls.
- `strategy/risk_signals.py`: allow, scale, block, or flatten decisions with
  reasons; no order submission.
- `strategy/costs.py`: directional naive and observed cost calculations.
- `strategy/portfolio.py`: combine maturity targets and enforce portfolio
  constraints.
- `data_pipeline/*`: acquire, canonicalize, validate, and write data; never
  decide trades.
- `backtesting/*`: replay time, simulate fills, account, and report; never
  reimplement signals or sizing.
- `agents/shared/*`: obtain paper state, invoke strategy/policy, reconcile, log,
  and route paper orders.
- `agents/agent_N/policy.py`: only the incremental behavior specific to that
  agent.

## Canonical directory and CSV contracts

CSV files are partitioned by source and year or paper-run ID when a single file
would become difficult to inspect. Filenames use lowercase snake case. Files
are UTF-8, comma-delimited, have one header, ISO times, deterministic sort, and
no implicit index.

### Historical rates

Path:
`data/source/quantt/rates/rates_YYYY.csv`

Columns:

```text
observation_date,source,series_id,maturity,rate_bps
```

Unique key:
`observation_date,source,series_id,maturity`

### Historical futures settlements

Path:
`data/source/quantt/futures/futures_settlements_YYYY.csv`

Columns:

```text
observation_date,source,instrument_id,settlement_price,dv01_usd_per_bp
```

Unique key:
`observation_date,source,instrument_id`

DV01 may be omitted from this dataset and stored in contract risk when it is
not time-varying. It must not be duplicated in both files for the same
instrument/date.

### Contract reference

Path:
`data/canonical/reference/contracts.csv`

Columns:

```text
instrument_id,source,asset_class,root,contract_month,maturity,currency,exchange,price_multiplier,tick_size,valid_from,valid_to
```

Unique key:
`instrument_id,valid_from`

### Contract risk

Path:
`data/canonical/reference/contract_risk_YYYY.csv`

Columns:

```text
observation_date,instrument_id,dv01_usd_per_bp,rate_sensitivity_sign,dv01_method
```

Unique key:
`observation_date,instrument_id`

### Canonical daily market input

Path:
`data/canonical/market/daily_market_YYYY.csv`

Columns:

```text
observation_date,series_id,instrument_id,value,value_unit,source_observation_time_utc,source
```

Exactly one of `series_id` and `instrument_id` is populated. The long format
prevents the current raw/signal/risk chain from copying dozens of unrelated
columns into every derived file.

### IBKR paper quotes

Path:
`data/paper/agent_N/run_id/quotes.csv`

Columns:

```text
timestamp_utc,instrument_id,bid_price,ask_price,bid_size,ask_size
```

If the approved strategy consumes last trade, settlement, or market-data
quality flags, add only those consumed fields and update the schema version.

### Paper decisions

Path:
`data/paper/agent_N/run_id/decisions.csv`

Columns:

```text
decision_id,timestamp_utc,agent_id,strategy_version,config_hash,maturity,prior_state,new_state,direction,reason_code,signal_value,signal_unit
```

### Paper orders

Path:
`data/paper/agent_N/run_id/orders.csv`

Columns:

```text
order_ref,decision_id,created_at_utc,instrument_id,side,quantity,order_type,time_in_force,status,ibkr_order_id
```

### Paper fills

Path:
`data/paper/agent_N/run_id/fills.csv`

Columns:

```text
fill_id,order_ref,fill_time_utc,instrument_id,side,quantity,fill_price,commission_usd
```

### Paper positions

Path:
`data/paper/agent_N/run_id/positions.csv`

Columns:

```text
timestamp_utc,instrument_id,quantity,average_cost,market_price,unrealized_pnl_usd,realized_pnl_usd
```

### Backtest daily results

Path:
`data/results/backtests/run_id/daily.csv`

Columns:

```text
observation_date,gross_pnl_usd,transaction_cost_usd,financing_cost_usd,net_pnl_usd,equity_usd,drawdown_usd,drawdown_pct,gross_dv01_usd_per_bp,net_dv01_usd_per_bp
```

Trades, fills, and positions belong in separate CSVs in the same run directory
instead of widening `daily.csv`.

### Run manifest

Path:
`data/manifests/run_id.csv`

Columns:

```text
run_id,run_type,agent_id,strategy_version,config_hash,code_commit,input_manifest_hash,started_at_utc,ended_at_utc,row_count,status
```

One row describes one immutable run. Detailed input-file hashes are stored in
`data/manifests/run_id_inputs.csv` with:

```text
run_id,path,sha256,row_count,start_time,end_time,schema_version
```

## Data validation invariants

Every writer validates before replacing its output:

1. Header equals the approved schema and contains no duplicates.
2. Required values are present and numeric/string types are valid.
3. Units are explicit and consistent.
4. Unique keys have no duplicates.
5. Timestamps parse, are normalized, and sort deterministically.
6. Bid is not greater than ask; sizes and prices required by a decision are
   positive.
7. Contract validity includes the observation date.
8. No forward fill crosses a contract roll or fills a field whose contract
   forbids filling.
9. No source observation occurs after the decision timestamp using it.
10. Output is written to a temporary sibling, validated, and atomically
    replaces the destination only after success.
11. Manifest hashes, row counts, coverage, and schema version are recorded.
12. Secrets, account credentials, and unnecessary vendor fields never enter a
    CSV.

## Backtest contracts

- Backtests invoke the shared complete strategy without copied equations.
- Features at time \(t\) cannot use a value timestamped after the decision.
- Positions held from \(t-1\) earn the price change to \(t\).
- Orders fill no earlier than their declared fill rule.
- Turnover charges entries, exits, reversals, resizing, and both sides of rolls.
- Naive and realistic runs use identical market observations and signals; only
  execution/cost assumptions differ.
- Missing realistic data never becomes zero cost. It blocks the trade or uses
  an approved conservative fallback recorded in results.
- Output includes inactive and risk-blocked dates so denominator choices are
  auditable.
- Parameter sweeps report the full grid, not only the best run.
- A date-window report preserves prior-position accounting or explicitly
  starts flat and labels the warm-up rule.

## Paper-agent contracts

- Broker configuration contains paper endpoints only and rejects accounts
  without the approved paper prefix.
- Tests and development prompts use fake brokers and never transmit orders.
- The user deliberately starts every external paper run after reviewing its
  gate.
- Agents have unique client IDs, order-reference prefixes, run directories,
  and frozen configs.
- Agents do not run simultaneously in one account when their positions cannot
  be attributed independently. Use sequential windows or approved isolated
  paper accounts.
- IBKR is authoritative for orders, fills, and positions; local state is
  reconciled before decisions.
- Stale/missing data, reconciliation errors, limit breaches, and disconnection
  prevent new risk and may request flattening.
- Every submitted order traces to one logged decision and every fill traces to
  one order.
- Reruns are idempotent with respect to deterministic order references.
- Emergency and scheduled flattening are explicit operations and are tested
  with fakes before a paper run.

## Change-control contract

Any change to an equation, schema, sign, unit, decision clock, agent delta,
cost assumption, or promotion criterion requires:

1. A written proposed change and reason.
2. Updated hand example or schema sample.
3. Updated failing test before implementation.
4. Requirements review and technical review.
5. Manual approval at the relevant gate.
6. A new strategy/config/schema version; never rewrite the interpretation of an
   existing run.
