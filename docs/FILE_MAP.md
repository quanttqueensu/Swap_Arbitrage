File map

This map covers the maintained project flow:

`historical data -> signals -> risk and sizing -> strategy decisions -> backtest`

The supported historical run is:

```powershell
python -m backtesting --start auto --end auto
```

Results are written to `data/results/backtests/<run-id>/` as `manifest.csv`,
`daily.csv`, `decisions.csv`, `orders.csv`, `fills.csv`, `trades.csv`,
`positions.csv`, and `summary.csv`.

## Configuration and shared contracts

| File | Key functions / classes | Responsibility |
| --- | --- | --- |
| `config.py` | Configuration constants | Legacy research-pipeline paths, source settings, maturity mappings, sizing constants, and risk limits. |
| `data_pipeline/contracts.py` | `ColumnContract`, `CsvContract`, `SCHEMAS`, `validate_csv()` | Defines the canonical CSV schemas and validates headers, types, keys, ordering, and row rules. |
| `strategy/models.py` | `MarketSnapshot`, `SpreadObservation`, `SignalDecision`, `TargetPosition`, `RiskDecision`, `OrderIntent`, `PaperPosition`, `WorkingOrder` | Typed records shared by the strategy, backtest, and paper-data layers. |
| `strategy/__init__.py` | Public strategy exports | Provides the supported strategy types and calculations to other packages. |

## Historical data and canonicalization

| File | Key functions / classes | Responsibility |
| --- | --- | --- |
| `data_pipeline/historical_data/historical_data_builder.py` | `equivalent_par_sofr_swap_rate_bps()`, `build_rates_dataset()`, `get_eris_public_swap_data()`, `get_public_treasury_futures_prices()`, `build_raw_price_data()`, `main()` | Fetches and assembles historical rates, swap-futures, and Treasury-futures data; selects daily Eris rows and converts settlement inputs into equivalent par rates. |
| `data_pipeline/historical_data/canonicalize.py` | `canonicalize_rates()`, `canonicalize_futures()`, `canonicalize_daily_market()` | Converts source datasets into canonical rate, contract, and daily-market records while retaining source timing. |
| `data_pipeline/historical_data/__init__.py` | Package marker | Identifies the historical-data package. |
| `clean_data.py` | Data-cleaning helpers | Legacy DataFrame cleaning utilities used by the research pipeline. |

## Signal and risk pipelines

| File | Key functions / classes | Responsibility |
| --- | --- | --- |
| `signal_pipeline.py` | `rolling_zscore()`, `add_funding_spread_proxy()`, `add_proxy_signal()`, `build_signal_columns()`, `build_signal_data()`, `main()` | Builds causal rate-based swap-spread signals and maturity ranking from equivalent Eris par rates and DGS2/DGS5 Treasury-rate proxies; price residuals are diagnostics. |
| `risk_pipeline.py` | `add_risk_budget_for_maturity()`, `add_contract_sizing_for_maturity()`, `enforce_gross_dv01_cap()`, `build_risk_columns()`, `build_risk_data()`, `main()` | Adds rate-spread volatility scaling, DV01-aware contract sizing, portfolio caps, and risk-allowance state. |

## Shared strategy calculations

| File | Key functions / classes | Responsibility |
| --- | --- | --- |
| `strategy/spread.py` | `fixed_swap_spread_bps()`, `funding_spread_bps()`, `net_opportunity_bps()`, `dv01_hedge_quantities()`, `basket_pnl_usd()` | Core spread, funding, hedge, turnover, and P&L calculations. |
| `strategy/signal_generation.py` | `causal_zscore()`, `signal_transition()`, `generate_signal_decision()`, `rank_opportunities()` | Converts a market observation and prior state into causal signal decisions and ranked opportunities. |
| `strategy/position_sizing.py` | `volatility_scale()`, `signal_strength_scale()`, `liquidity_scale()`, `scaled_target_dv01()`, `build_target_position()` | Converts approved signals and constraints into bounded, hedge-aware target positions. |
| `strategy/risk_signals.py` | `evaluate_risk()` | Decides whether a proposed target is permitted under risk constraints. |
| `strategy/portfolio.py` | `portfolio_dv01()`, `select_portfolio_targets()` | Combines maturity targets and enforces portfolio-level DV01 limits. |
| `strategy/costs.py` | `CostEstimate`, `naive_cost()`, `observed_cost()` | Produces estimated and observed execution-cost records. |

## Historical backtesting and reports

| File | Key functions / classes | Responsibility |
| --- | --- | --- |
| `backtesting/engine.py` | `ReplayEvent`, `StrategyResult`, `BacktestResult`, `run_backtest()` | General event-replay simulator: marks held positions, applies costs, simulates delayed orders and fills, and produces accounting records. |
| `backtesting/historical.py` | `_events_from_frame()`, `_historical_strategy()`, `run_historical_backtest()` | Adapts dated historical signal/risk rows into `ReplayEvent` values and runs the canonical historical replay. |
| `backtesting/assumptions.py` | `NaiveAssumptions`, `NAIVE_ASSUMPTIONS` | Defines the explicit fill, cost, financing, and roll assumptions used by the simulator. |
| `backtesting/reports.py` | `write_results()` | Renders, validates, and atomically writes the canonical backtest report set. |
| `backtesting/__main__.py` | `parse_args()`, `self_check()`, `main()` | Implements `python -m backtesting`, its arguments, and offline self-check. |
| `backtesting/__init__.py` | Public backtesting exports | Makes the main simulation and historical-run functions importable. |

## IBKR paper-data infrastructure

| File | Key functions / classes | Responsibility |
| --- | --- | --- |
| `data_pipeline/live_data_pipeline/ibkr_paper_source.py` | `PaperSessionConfig`, `PaperSafetyError`, `IbkrPaperRecorder.validate_session()`, `IbkrPaperRecorder.request_quotes()`, `IbkrPaperRecorder.record_quote()`, `IbkrPaperRecorder.record_order()`, `IbkrPaperRecorder.record_fill()`, `IbkrPaperRecorder.record_positions()` | Validates the paper-only broker session, requests quotes through `reqMktData()`, and normalizes broker data into canonical paper records. The caller supplies the IBKR position snapshot to `record_positions()`. |
| `data_pipeline/live_data_pipeline/paper_store.py` | `PaperEventStore.path_for()`, `write()` | Validates, deduplicates, serializes, and atomically writes paper quotes, orders, fills, and positions under `data/paper/agent_N/run_id/`. |
| `data_pipeline/live_data_pipeline/__init__.py` | Package marker | Identifies the IBKR paper-data package. |

## IBKR connection and paper-order infrastructure

| File | Key functions / classes | Responsibility |
| --- | --- | --- |
| `agents/agent_0/config.py` | `get_agent_account_id()`, `assert_paper_only_settings()` | Enforces the localhost, port `7497`, client ID `30`, and `DU...` paper-account safety boundary. |
| `agents/agent_0/broker.py` | `connect()`, `disconnect()`, `validate_managed_account()`, `submit_order()`, `cancel_all_orders()` | Wraps `IB.connect()`, `managedAccounts()`, `placeOrder()`, `reqAllOpenOrders()`, and `reqGlobalCancel()` with paper-session checks. |
| `agents/agent_0/contracts.py` | `get_instrument()`, `pick_front_contracts()`, `pick_eris_contracts()`, `resolve_futures()` | Selects valid futures expiries and calls `reqContractDetails()` plus `qualifyContracts()` to obtain usable IBKR contracts. |
| `agents/agent_0/orders.py` | `build_order()`, `load_orders()`, `save_orders()`, `roll_tracking()` | Creates scheduled paper orders and maintains the local upcoming/previous order ledgers. |
| `agents/agent_0/run.py` | `margin_reserve_ok()`, `fit_order_to_margin()`, `submit_plan()`, `queue_next_week()`, `cancel_all_working_orders()`, `main()` | Reconciles visible working orders, previews margin through `whatIfOrder()`, submits guarded paper orders, or cancels all visible working orders. |
| `agents/agent_0/random_policy.py` | `RandomPolicy.build_week_plan()` | Produces the isolated Agent 0 random paper-order schedule. It is not the swap-arbitrage strategy. |
| `agents/agent_0/sizing.py` | `load_sizing_caps()` | Derives paper-experiment quantity caps from historical strategy sizing data. |
| `agents/agent_0/models.py` | `AgentInstrument`, `SizingCap`, `QueuedOrder` | Typed records used by the paper-order experiment. |
| `agents/agent_0/SETTINGS.md` | Operator checklist and commands | Documents the human-operated, paper-only setup and cancellation warning. |

## Documentation and tests

| File / directory | Key coverage | Responsibility |
| --- | --- | --- |
| `README.md` | Setup and supported commands | Short project entry point and operator orientation. |
| `docs/TECHNICAL_DOCUMENTATION.md` | Architecture, rate-based signal, backtest flow, paper boundary, runbook | Main technical reference, including proxy limitations and futures-price P&L. |
| `docs/data/canonical-schemas.md` | Canonical CSV definitions | Describes the datasets governed by `data_pipeline/contracts.py`. |
| `docs/tests/test_canonicalize.py` | Historical canonicalization | Verifies rate, futures, market, and timing conversion. |
| `docs/tests/test_signal_generation.py`, `test_spread.py`, `test_position_sizing_and_risk.py`, `test_portfolio.py` | Strategy logic | Verifies calculations, signals, sizing, risk, and portfolio limits. |
| `docs/tests/test_dv01_pipeline.py` | Historical conversion and rate-spread signals | Verifies Eris equivalent-par-rate conversion, DGS2/DGS5 proxy spreads, signal routing, sizing inputs, and technical-documentation acceptance. |
| `docs/tests/test_historical_backtest.py`, `test_naive_backtest.py` | Replay and reports | Verifies historical adaptation, causal timing, fills, costs, roll handling, rate-spread diagnostic P&L isolation, and report outputs. |
| `docs/tests/test_ibkr_paper_recorder.py` | Paper-data safety | Verifies IBKR paper-session checks, data normalization, privacy, and stored records. |
| `docs/tests/test_schema_contracts.py` | CSV contracts | Verifies schemas, validators, and paper/backtest distinctions. |
| `docs/tests/test_backtesting_documentation.py` | User-facing backtest documentation | Guards the canonical `python -m backtesting` workflow in maintained documentation. |
| `agents/agent_0/tests/` | Paper-order experiment | Verifies paper routing, contract selection, margin checks, reconciliation, and cancellation behavior without opening a broker connection. |
