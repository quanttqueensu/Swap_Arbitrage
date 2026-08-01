# Canonical CSV Schemas

## Freeze

- Schema version: `1.0.0`
- Approved prerequisite: MG2, 2026-07-31
- Executable catalog: `data_pipeline/contracts.py`
- Initial supported research scope: 2Y and 5Y; 10Y and 30Y remain unavailable.
- Encoding: UTF-8, comma-delimited, one exact header, no implicit index.
- Partitioning: calendar year for historical/source tables and `run_id` for
  run artifacts.
- Ordering: ascending by the exact fields shown below.
- Missing values: an empty field is legal only when its column is marked
  nullable. The strings `NA`, `null`, and `None` are data, not missing markers.
- Types: `date` is ISO `YYYY-MM-DD`; `datetime_utc` is ISO 8601 ending in `Z`;
  `decimal` is finite base-10 text; `integer` is signed base-10 text; `string`
  is nonempty UTF-8 text unless nullable.
- Versioning: changing a field, key, unit, clock, or meaning creates a new
  schema version under the change-control contract.

The P21 fixture validator enforces exact header, lexical types, required
values, key, ordering, and the table-local rules below. Cross-table contract
validity (an instrument's validity interval covering an observation) and the
decision cutoff (no source observation or publication after the consuming
decision) require reference/snapshot context and are explicitly deferred to
P22-P24 writer/integration validators. Those later writers must enforce them
before atomic replacement. Manifest hashes and coverage are also required at
P22-P24.
Secrets, credentials, account identifiers, and unused vendor columns are
forbidden.

## Column lineage and justification

Every `ColumnContract` in the executable catalog carries three additional
required attributes: `reason`, `source_or_derivation`, and `consumers`.
Identity fields identify/order rows; timestamps define causal or interval
boundaries; provenance fields preserve interpretation; risk/accounting fields
carry their named measure; broker IDs exist only for paper reconciliation.
Each field names one justifying consumer instead of inheriting every table
consumer. Catalog tests reject placeholders and assert representative exact
lineage for causal availability and broker reconciliation. This is the
complete executable canonical-column mapping; P20's
`current-column-lineage.csv` remains the separate mapping for legacy columns.

## Source and canonical market data

### `historical_rates` 1.0.0

- Path: `data/source/quantt/rates/rates_YYYY.csv`
- Header: `observation_date,source,series_id,maturity,rate_bps`
- Types/units: `date/date`, `string/source_id`, `string/series_id`,
  `string/maturity`, `decimal/basis_points`
- Key and ordering: `observation_date,source,series_id,maturity`
- Frequency: daily by year
- Retention: immutable source capture
- Consumers: Quantt adapter and canonicalizer
- Sample: `2026-07-30,UST,US-CMT,2Y,388.5`

### `historical_futures_settlements` 1.0.0

- Path: `data/source/quantt/futures/futures_settlements_YYYY.csv`
- Header: `observation_date,source,instrument_id,settlement_price,dv01_usd_per_bp`
- Types/units: `date/date`, `string/source_id`, `string/instrument_id`,
  `decimal/price_points`, nullable `decimal/usd_per_bp`
- Key and ordering: `observation_date,source,instrument_id`
- Frequency: daily by year
- Retention: immutable source capture
- Consumers: Quantt adapter and canonicalizer
- Sample: `2026-07-30,ERIS,ERIS-YIT-202609,99.25,39.8`
- DV01 rule: DV01 may be blank and supplied by `contract_risk`; it must not be
  duplicated in both schemas for the same instrument/date. When populated it
  is strictly positive.

### `contract_reference` 1.0.0

- Path: `data/canonical/reference/contracts.csv`
- Header: `instrument_id,source,asset_class,root,contract_month,maturity,currency,exchange,price_multiplier,tick_size,valid_from,valid_to`
- Types/units: identifiers and classifications are strings; multipliers are
  `decimal/usd_per_price_point`; tick size is `decimal/price_points`; validity
  bounds are dates.
- Key and ordering: `instrument_id,valid_from`
- Frequency: on approved reference change
- Retention: every validity interval
- Consumers: canonicalizer, sizing, replay, and paper adapter
- Sample: `ERIS-YIT-202609,ERIS,swap_future,YIT,2026-09,2Y,USD,ERIS,1000,0.0005,2026-06-01,2026-09-30`

### `contract_risk` 1.0.0

- Path: `data/canonical/reference/contract_risk_YYYY.csv`
- Header: `observation_date,instrument_id,dv01_usd_per_bp,rate_sensitivity_sign,dv01_method`
- Types/units: `date/date`, `string/instrument_id`, `decimal/usd_per_bp`,
  `integer/sign`, `string/method`
- Key and ordering: `observation_date,instrument_id`
- Frequency: daily by year
- Retention: with every consuming input manifest
- Consumers: sizing, risk, replay, and paper adapter
- Sample: `2026-07-30,ERIS-YIT-202609,39.8,-1,eris_settlement_dv01`
- Rule: `rate_sensitivity_sign` is exactly `-1` or `+1`.
- Rule: `dv01_usd_per_bp` is strictly positive.

### `daily_market` 1.0.0

- Path: `data/canonical/market/daily_market_YYYY.csv`
- Header: `observation_date,series_id,instrument_id,value,value_unit,source_observation_time_utc,available_at_utc,source,classification,proxy_label`
- Types/units: date; nullable series/instrument identifiers; finite decimal;
  declared unit; two UTC timestamps; source; classification; nullable label.
- Key and ordering: `observation_date,series_id,instrument_id,source,available_at_utc`
- Frequency: daily by year
- Retention: with every consuming input manifest
- Consumers: spread equations, causal signals, and replay
- Sample: `2026-07-30,US-CMT-2Y,,388.5,basis_points,2026-07-30T20:00:00Z,2026-07-30T20:01:00Z,UST,exact,`
- Rules: exactly one of `series_id` and `instrument_id`; publication
  availability cannot precede the source observation; classifications are
  exactly `exact`, `proxy`, `assumed`, or `unavailable`; only `proxy` rows have
  a nonempty proxy label. No forward fill crosses a gap or roll.

## Decisions and execution telemetry

### `paper_quotes` 1.0.0

- Path: `data/paper/agent_N/run_id/quotes.csv`
- Header: `timestamp_utc,instrument_id,bid_price,ask_price,bid_size,ask_size`
- Types/units: UTC, instrument ID, two decimal price points, two decimal
  contract sizes
- Key and ordering: `timestamp_utc,instrument_id`
- Frequency/retention: event append; immutable after run close
- Consumers: paper adapter, costs, and risk
- Rules: prices and sizes are positive; bid cannot exceed ask.
- Sample: `2026-07-30T20:00:00Z,ERIS-YIT-202609,99.24,99.25,10,12`

### `paper_decisions` 1.0.0

- Path: `data/paper/agent_N/run_id/decisions.csv`
- Header: `decision_id,timestamp_utc,agent_id,strategy_version,config_hash,maturity,prior_state,new_state,direction,reason_code,signal_value,signal_unit`
- Nullable: `signal_value` and `signal_unit` when no
  signal value exists
- Key: `decision_id`; ordering: `timestamp_utc,decision_id`
- Frequency/retention: event append; immutable run artifact
- Consumers: paper adapter and risk
- Sample: `d-1,2026-07-30T20:01:00Z,agent_1,swap-arb-v1,aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,2Y,0,1,1,enter_traditional,2.1,zscore`
- Rule: prior state, new state, and direction are each in `{-1,0,+1}`.
- Rule: `signal_value` and `signal_unit` are either both populated or both
  empty.

### `paper_orders` 1.0.0

- Path: `data/paper/agent_N/run_id/orders.csv`
- Header: `order_ref,decision_id,created_at_utc,instrument_id,side,quantity,order_type,time_in_force,status,ibkr_order_id`
- Types/units: identifiers/strings, UTC, integer contracts; broker order ID is
  nullable before broker acknowledgement
- Key: `order_ref`; ordering: `created_at_utc,order_ref`
- Frequency/retention: event append/status reconciliation; immutable after run
- Consumer: paper adapter
- Sample: `o-1,d-1,2026-07-30T20:01:01Z,ERIS-YIT-202609,BUY,2,MKT,DAY,planned,`
- Rule: `BUY` has positive signed quantity; `SELL` has negative signed
  quantity; zero and other sides are invalid.

### `paper_fills` 1.0.0

- Path: `data/paper/agent_N/run_id/fills.csv`
- Header: `fill_id,order_ref,fill_time_utc,instrument_id,side,quantity,fill_price,commission_usd`
- Types/units: identifiers/strings, UTC, integer contracts, decimal price
  points, decimal USD
- Key: `fill_id`; ordering: `fill_time_utc,fill_id`
- Frequency/retention: event append; immutable after run
- Consumers: paper reconciliation and risk
- Sample: `f-1,o-1,2026-07-30T20:01:02Z,ERIS-YIT-202609,BUY,2,99.25,1.20`
- Rule: side and signed quantity follow the same BUY-positive/SELL-negative
  convention.

### `paper_positions` 1.0.0

- Path: `data/paper/agent_N/run_id/positions.csv`
- Header: `timestamp_utc,instrument_id,quantity,average_cost,market_price,unrealized_pnl_usd,realized_pnl_usd`
- Types/units: UTC, identifier, integer contracts, two decimal price points,
  two decimal USD values
- Key and ordering: `timestamp_utc,instrument_id`
- Frequency/retention: snapshot append; immutable after run close
- Consumers: paper reconciliation and risk
- Sample: `2026-07-30T20:02:00Z,ERIS-YIT-202609,2,99.25,99.26,20,0`

## Backtest results

### `backtest_decisions` 1.0.0

- Path: `data/results/backtests/run_id/decisions.csv`
- Header: `decision_id,timestamp_utc,strategy_version,config_hash,maturity,prior_state,new_state,direction,reason_code,signal_value,signal_unit`
- Nullable: `signal_value` and `signal_unit` when no signal value exists
- Key: `decision_id`; ordering: `timestamp_utc,decision_id`
- Frequency/retention: event append; immutable run artifact
- Consumers: replay and reports
- Sample: `d-1,2026-07-30T20:01:00Z,swap-arb-v1,aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,2Y,0,1,1,enter_traditional,2.1,zscore`
- Rule: prior state, new state, and direction are each in `{-1,0,+1}`.
- Rule: `signal_value` and `signal_unit` are either both populated or both
  empty.

### `backtest_orders` 1.0.0

- Path: `data/results/backtests/run_id/orders.csv`
- Header: `order_ref,decision_id,created_at_utc,instrument_id,side,quantity,order_type,time_in_force,status`
- Key: `order_ref`; ordering: `created_at_utc,order_ref`
- Frequency/retention: event append; immutable run artifact
- Consumers: replay and reports
- Sample: `o-1,d-1,2026-07-30T20:01:01Z,ERIS-YIT-202609,BUY,2,MKT,DAY,planned`
- Rule: `BUY` has positive signed quantity; `SELL` has negative signed
  quantity; zero and other sides are invalid.

### `backtest_fills` 1.0.0

- Path: `data/results/backtests/run_id/fills.csv`
- Header: `fill_id,order_ref,fill_time_utc,instrument_id,side,quantity,fill_price,commission_usd`
- Key: `fill_id`; ordering: `fill_time_utc,fill_id`
- Frequency/retention: event append; immutable run artifact
- Consumers: accounting and reports
- Sample: `f-1,o-1,2026-07-30T20:01:02Z,ERIS-YIT-202609,BUY,2,99.25,1.20`
- Rule: side and signed quantity follow the same BUY-positive/SELL-negative
  convention.

### `backtest_daily` 1.0.0

- Path: `data/results/backtests/run_id/daily.csv`
- Header: `observation_date,gross_pnl_usd,transaction_cost_usd,financing_cost_usd,net_pnl_usd,equity_usd,drawdown_usd,drawdown_pct,gross_dv01_usd_per_bp,net_dv01_usd_per_bp`
- Key and ordering: `observation_date`
- Frequency/retention: daily; immutable run artifact
- Consumers: accounting and reports
- Sample: `2026-07-30,100,5,1,94,1000094,-10,-0.001,6000,25`

### `backtest_trades` 1.0.0

- Path: `data/results/backtests/run_id/trades.csv`
- Header: `trade_id,decision_id,maturity,direction,opened_at_utc,closed_at_utc,gross_pnl_usd,cost_usd,net_pnl_usd`
- Nullable: `closed_at_utc` while open
- Key: `trade_id`; ordering: `opened_at_utc,trade_id`
- Frequency/retention: event append; immutable run artifact
- Consumers: accounting and reports
- Sample: `t-1,d-1,2Y,1,2026-07-30T20:01:02Z,,0,1.20,-1.20`
- Rule: trade direction is exactly `-1` or `+1`.

### `backtest_positions` 1.0.0

- Path: `data/results/backtests/run_id/positions.csv`
- Header: `observation_date,instrument_id,quantity,market_price,market_value_usd,unrealized_pnl_usd,realized_pnl_usd`
- Key and ordering: `observation_date,instrument_id`
- Frequency/retention: daily; immutable run artifact
- Consumers: accounting and reports
- Sample: `2026-07-30,ERIS-YIT-202609,2,99.26,198520,20,0`

### `backtest_summary` 1.0.0

- Path: `data/results/backtests/run_id/summary.csv`
- Header: `run_id,strategy_version,start_date,end_date,row_count,trade_count,net_pnl_usd,ending_equity_usd,max_drawdown_usd,max_drawdown_pct`
- Key and ordering: `run_id`
- Frequency/retention: once per completed run; immutable run artifact
- Consumer: reports
- Sample: `run-1,swap-arb-v1,2026-01-02,2026-07-30,145,12,500,1000500,-200,-0.02`

## Manifests

### `run_manifest` 1.0.0

- Path: `data/manifests/run_id.csv`
- Header: `run_id,run_type,agent_id,strategy_version,config_hash,code_commit,input_manifest_hash,started_at_utc,ended_at_utc,row_count,status`
- Nullable: `agent_id` for backtests; `ended_at_utc` while running
- Key and ordering: `run_id`
- Frequency/retention: one row per run; permanent audit record
- Consumers: manifest writer, replay reports, and paper runner
- Sample: `run-1,backtest,,swap-arb-v1,aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,0123456789abcdef0123456789abcdef01234567,bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb,2026-07-30T20:00:00Z,2026-07-30T20:10:00Z,145,complete`

### `run_inputs` 1.0.0

- Path: `data/manifests/run_id_inputs.csv`
- Header: `run_id,path,sha256,row_count,start_time,end_time,schema_version`
- Key and ordering: `run_id,path`
- Frequency/retention: one row per run input; permanent audit record
- Consumers: manifest writer, replay reports, and paper runner
- Sample: `run-1,data/canonical/market/daily_market_2026.csv,cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc,145,2026-01-02,2026-07-30,1.0.0`

## Known limits at MG3

This freeze defines shapes and validation, not source availability. Exact 2Y
and 5Y CMS, collateral-consistent repo, the production business calendar,
forward funding, and validated 10Y/30Y inputs remain unavailable. Existing
continuous Treasury futures and EFFR-SOFR remain explicitly labelled proxies.
P22-P24 must prove source meaning, row conversions, atomic writes, manifests,
and deterministic staging before these schemas contain canonical project data.
