# Swap Arbitrage technical documentation

## Quick start

Python 3.12 is the supported interpreter. From PowerShell in the repository
root, create an environment, install the pinned dependencies, and run the
offline checks:

```powershell
& "C:\Path\To\Python312\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip check
python signal_pipeline.py --self-check
python risk_pipeline.py --self-check
python -m backtesting --self-check
```

These checks use fixed examples. They do not download data, connect to IBKR, or
show that the strategy is profitable.

## Operating steps

### Prepare historical data

Refresh the public rate and Eris data, rebuild the raw files, and calculate
risk-sized signals:

```powershell
python risk_pipeline.py --refresh-raw --treasury --eris
```

This command needs network access. Its results depend on the coverage and
availability of the public sources. To rebuild only the signal and risk data
from existing raw files, run:

```powershell
python risk_pipeline.py --refresh-signals
```

### Run the historical backtest

After the historical input exists, run:

```powershell
python -m backtesting --start auto --end auto
```

Results are written to `data/results/backtests/<run-id>/`. The current
historical adapter and roll and liquidity settings are research tools, so treat
the results as diagnostic rather than proof of executable performance.

### Run the Agent 0 paper experiment

Agent 0 is separate from the swap-arbitrage strategy. It creates a constrained,
random weekly paper-order plan and does not use strategy signals.

Before running it, complete the checklist in
[`agents/agent_0/SETTINGS.md`](../agents/agent_0/SETTINGS.md), start TWS or IB
Gateway in paper mode, and set the approved paper account in the local
environment:

```powershell
$env:AGENT0_IBKR_ACCOUNT = "<paper-account-id>"
.venv\Scripts\python.exe agents\agent_0\run.py
```

The runner checks the paper connection, visible account, contracts, working
orders, and margin reserve before it submits an order.

To cancel every working order visible to the paper session, including manual
orders and orders from other API clients, run:

```powershell
.venv\Scripts\python.exe agents\agent_0\run.py --cancel-all
```

Cancellation resets local upcoming orders to `planned`; it does not create a
replacement plan.


## System reference

### Components

| Path | Responsibility |
| --- | --- |
| `strategy/models.py` | Defines shared strategy data structures, including `MarketSnapshot` and `OrderIntent` |
| `strategy/spread.py` | Calculates swap/Treasury spreads and trading costs |
| `strategy/signal_generation.py` | Decides whether to enter, exit, reverse, or hold |
| `strategy/position_sizing.py` | Calculates hedge amounts and contract quantities within risk limits |
| `strategy/risk_signals.py` | Allows, reduces, blocks, or flattens trading based on risk conditions |
| `strategy/costs.py` | Estimates trading costs |
| `strategy/portfolio.py` | Selects trades without exceeding portfolio DV01 limits |
| `data_pipeline/contracts.py` | Defines and validates required data formats |
| `data_pipeline/historical_data/` | Downloads, cleans, and organizes historical data |
| `data_pipeline/live_data_pipeline/` | Records IBKR paper quotes, orders, fills, and positions |
| `backtesting/engine.py` | Runs the simulation and accounting engine |
| `backtesting/historical.py` | Converts historical signal and risk data into replay events |
| `backtesting/main.py` | Provides `python -m backtesting` and the offline self-check |
| `agents/agent_0/` | Runs the random weekly paper-trading experiment |
| `docs/tests/` | Tests strategy and data behavior |
| `docs/verification/` | Stores evidence from earlier verification runs |

The strategy refuses new trades when required data is missing, invalid, or
stale. Risk limits can also block trading because of DV01, capacity, losses,
drawdown, margin, broker state, reconciliation, or contract rolls. These
controls apply in both backtests and paper trading.

### Data sources

| Provider | Data | Project function |
| --- | --- | --- |
| U.S. Treasury | Daily constant-maturity yield curves | `get_treasury_data()` |
| New York Fed SOFR API | SOFR history | `get_nyfed_rate("SOFR", ...)` |
| New York Fed EFFR API | EFFR history | `get_nyfed_rate("EFFR", ...)` |
| Eris Markets public archive | Swap-futures settlements, tickers, and published DV01 | `get_eris_public_swap_data()` |
| Yahoo Finance chart API | Research prices for `ZT=F` and `ZF=F` | `get_public_treasury_futures_prices()` |
| IBKR socket API | Paper quotes, contracts, positions, orders, fills, and margin previews | Paper-trading infrastructure only |

IBKR uses the local paper endpoint `127.0.0.1:7497`. It is not a historical
data source.

### Historical data layout

- `data/rates/rates_YYYY.csv`: interest rates in basis points
- `data/futures/futures_settlements_YYYY.csv`: futures settlement prices
- `data/contract_risk/contracts.csv`: contract details and effective dates
- `data/contract_risk/contract_risk_YYYY.csv`: contract DV01 and rate sensitivity
- `data/market/daily_market_YYYY.csv`: daily market data

`historical_data_builder.py` downloads the source data. `canonicalize.py`
converts it to the standard project format. Keeping these steps separate lets
tests use saved samples without making network requests.

### Rate-based swap-spread signal

For each maturity, the historical builder selects one daily Eris settlement
row. It retains `FinalSettlementPrice`, `Coupon (%)`, `PastFxdFltPmts (B)`,
`ErisPAI (C)`, `PV01`, `EffectiveDate`, `MaturityDate`, and `LastTradeDate`.
These feed `eris_swap_2y_fixed_coupon_pct` and
`eris_swap_5y_fixed_coupon_pct`; the corresponding `_b_usd`, `_c_usd`,
`_pv01_usd_per_bp`, `_effective_date`, `_maturity_date`, and `_last_trade_date`
fields; and `eris_swap_2y_equivalent_par_rate_bps` and
`eris_swap_5y_equivalent_par_rate_bps`. On the same date, DGS2/DGS5 provide
the Treasury-rate inputs.

With price in exchange points, B and C in USD, PV01 in USD per bp, and coupon
in percent, the conversion is:

```text
A_usd = (FinalSettlementPrice - 100 - B + C) * 1000
equivalent_par_rate_pct = Coupon (%) - (A_usd / PV01) / 100
eris_swap_2y_equivalent_par_rate_bps = equivalent_par_rate_pct * 100
eris_swap_5y_equivalent_par_rate_bps = equivalent_par_rate_pct * 100
treasury_rate_proxy_bps_2y = DGS2 * 100
treasury_rate_proxy_bps_5y = DGS5 * 100
swap_spread_bps_2y = eris_swap_2y_equivalent_par_rate_bps - treasury_rate_proxy_bps_2y
swap_spread_bps_5y = eris_swap_5y_equivalent_par_rate_bps - treasury_rate_proxy_bps_5y
```

The rolling z-score of each `swap_spread_bps_*` drives entry and exit; price
residuals remain diagnostics. DGS2/DGS5 are Treasury constant-maturity rate
proxies, not CTD-implied yields and not forward-start/IMM-aligned Treasury
rates.

Intentionally deferred:

- CTD-implied Treasury yields and delivery-basket/conversion-factor modeling.
- IMM-forward-start curve matching between each Eris swap and Treasury comparator.
- New market-data vendors or a separate Eris curve download.
- Database, pipeline, framework, or infrastructure redesign.

### IBKR operational API

All broker activity is paper-only. Connection, order submission, and
cancellation use guarded helpers. `IbkrPaperRecorder` validates and records
broker data but does not connect, submit, cancel, or request positions itself.

| Need | Project function | IBKR or `ib_insync` call | Notes |
| --- | --- | --- | --- |
| Connect to paper session | `agents.agent_0.broker.connect(account_id)` | `IB.connect()`, `isConnected()`, `managedAccounts()` | Requires localhost, port `7497`, client ID `30`, and a `DU...` account |
| Find futures contracts | `agents.agent_0.contracts.resolve_futures(ib, instrument)` | `reqContractDetails()`, `qualifyContracts()` | Returns eligible qualified contracts and their IBKR IDs |
| Inspect working orders | `ib.reqAllOpenOrders()` | `reqAllOpenOrders()` | Used before order submission |
| Preview margin | `fit_order_to_margin(...)` | `whatIfOrder(contract, order)` | Reduces quantity until the margin reserve is met |
| Submit an order | `agents.agent_0.broker.submit_order(...)` | `placeOrder(contract, order)` | Checks paper settings and waits for broker status |
| Cancel visible orders | `agents.agent_0.broker.cancel_all_orders(ib)` | `reqGlobalCancel()`, `reqAllOpenOrders()` | Can also cancel manual orders and orders from other clients |
| Request quotes | `IbkrPaperRecorder.request_quotes(contracts)` | `reqMktData()` | Checks the paper session first |
| Record positions | `IbkrPaperRecorder.record_positions(...)` | `positions()` | Caller gets the snapshot; recorder validates and stores it |
| Record orders and fills | `record_order(...)`, `record_fill(...)` | Broker callbacks | Converts broker objects to canonical paper records |


## Historical backtest reference

`backtesting.historical.run_historical_backtest` converts stored signal and
risk data into typed `ReplayEvent` records, runs the accounting engine, and
writes the results. Use `--refresh-signals` to rebuild upstream inputs. The
`--self-check` option runs offline.

| Option | Default |
| --- | --- |
| `--run-id` | `historical-backtest` |
| `--output-root` | `data/results/backtests` |
| `--initial-equity` | `1000000` |
| `--start`; `--end` | `auto`; `auto` |
| `--bid-ask-half-spread-points` | `0.01` |
| `--commission-usd-per-contract` | `1` |
| `--slippage-points` | `0.005` |
| `--financing-usd-per-contract-day` | `0.10` |
| `--roll-usd-per-contract` | `1` |

For each event, the engine:

1. marks existing positions and applies financing costs;
2. processes eligible orders and their fills, rejections, or expiry;
3. runs the strategy using current positions and active orders;
4. saves new orders for a later event; and
5. records P&L, positions, and accounting data.

Each run contains:

- `manifest.csv`
- `daily.csv`
- `decisions.csv`
- `orders.csv`
- `fills.csv`
- `trades.csv`
- `positions.csv`
- `summary.csv`

The files are validated before they are saved. The manifest records versions,
date range, row counts, hashes, assumptions, and missing data.

If data is missing while a position is open, the run records the gap and uses
the available data. During a contract roll, it carries forward the previous
mark with `last_pre_roll_mark_zero_return` instead of inventing a cross-contract
return. 

Backtest assumptions and examples are tested in
`docs/tests/test_naive_backtest.py`.

## Paper-data lifecycle

`IbkrPaperRecorder` receives an already connected broker object. It checks the
local paper settings, connection, and managed account before it requests quotes
or records events.

`PaperEventStore` accepts only approved paper schemas under
`data/paper/agent_N/run_id/`. It rejects unsafe paths, account-like values,
secrets, conflicting duplicates, invalid order changes, bad types, and invalid
ordering. It validates a temporary sibling before replacing the destination.

Broker objects are untrusted. Normalization failures become generic
`PaperSafetyError` messages so exception chains do not retain accounts,
credentials, endpoints, or client IDs.

## Technical conventions

### Units and signs

- Rates and spreads: basis points (`_bps`)
- Raw rates: decimals (`_decimal`), converted at the input boundary
- Prices: exchange price points (`_price`)
- DV01: USD gained or lost for a 1 bp rate increase (`_dv01_usd_per_bp`)
- P&L and costs: USD (`_usd`)
- Event timestamps: timezone-aware UTC (`_utc`)
- Daily historical data: ISO dates
- Contract quantity: positive is long; negative is short

For a position held in the same contract:

`P&L = quantity × price multiplier × price change`

Basket P&L adds the P&L from each leg and subtracts transaction, financing, and
roll costs. A price change between different contracts is not normal
same-contract P&L. Historical P&L remains quantity × price multiplier × futures
price change. The Eris futures settlement price already reflects swap NPV,
coupon accrual/payment effects, and PAI, so coupon P&L is not added separately.

Net DV01 is the combined directional rate exposure. Gross DV01 is the sum of
absolute rate exposure. Hedge quantities aim to minimize residual DV01 while
respecting contract, liquidity, and portfolio limits.

Economic direction is not the same as exchange order side. One strategy
direction may need both long and short futures positions. The strategy equation
tests cover these sign rules.

### Timing

- A decision at time `t` uses only information available by `t`.
- Existing positions earn the price move into the next event.
- An order cannot fill before it becomes eligible.
- A reversal closes the old direction before opening the new one.
- Both sides of a reversal create turnover and costs.
- A contract roll includes closing and opening costs.

### Configuration and dependencies

| Item | Setting |
| --- | --- |
| Python | 3.12 |
| NumPy | 2.3.5 |
| pandas | 3.0.1 |
| `ib_insync` | 0.9.86 |
| Canonical schema version | 1.0.0 |
| Strategy equation version | `p10.strategy-equations.v1` |
| Position-sizing/risk version | `p33.position-sizing-risk.v1` |
| Agent 0 | Local IBKR paper session, port `7497`, client ID `30` |


## Testing and failure handling

The main offline tests are in `docs/tests` and `agents/agent_0/tests`:

- `test_strategy_equation_examples`: calculation and sign conventions
- `test_schema_contracts`: CSV headers, types, keys, ordering, and validation
- `test_ibkr_paper_recorder`: paper safety, privacy, broker data, and storage
- `test_naive_backtest`: replay timing, accounting, reversals, and reports
- `test_characterization`: Agent 0 planning, reconciliation, margin, and routing

## Glossary

| Term | Meaning |
| --- | --- |
| **Basis point (bp)** | 0.01 percentage points |
| **DV01** | Dollar change in value from a 1 bp rate change |
| **Fixed swap spread** | Swap rate minus the matching Treasury rate |
| **Funding spread** | Floating funding rate minus repo or funding cost |
| **Gross excess spread** | Swap spread minus expected funding cost |
| **Net opportunity** | Expected opportunity after estimated costs |
| **Traditional direction** | Receive-fixed, short-Treasury direction (`+1`) |
| **Reverse direction** | Opposite direction (`-1`) |
| **Decision timestamp** | Earliest time when all decision inputs were available |
| **`MarketSnapshot`** | Typed market information available at decision time |
| **`OrderIntent`** | Broker-independent description of a requested paper order |
| **Canonical data** | Data converted to approved, validated schemas |
| **Synthetic mechanics** | Test data used to check engine behavior |
| **Agent 0** | Random IBKR paper-order experiment, not the strategy |
| **Paper recorder** | Adapter that validates and records paper events |
| **Lifecycle trade** | Trade tracked from opening exposure through final closure |
| **Fail closed** | Refuse new risk or output when required validation fails |
