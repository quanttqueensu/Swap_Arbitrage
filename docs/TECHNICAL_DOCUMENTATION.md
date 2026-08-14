# Swap Arbitrage technical documentation

## Quick start

### Environment setup

Python 3.12 is the supported interpreter. Runtime dependencies are pinned in
[`requirements.txt`](../requirements.txt): NumPy 2.3.5, pandas 3.0.1, and
ib_insync 0.9.86.

From PowerShell in the repository root, create a fresh environment:

```powershell
& "C:\Path\To\Python312\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip check
```

### Offline verification commands

After activating a valid environment, these commands are supported and make no
external or broker connection:

```powershell
python -m unittest docs.tests.test_naive_backtest -v
python -m unittest docs.tests.test_schema_contracts -v
python -m unittest discover -s docs/tests -v
python -m unittest discover -s agents/agent_0/tests -v
python -m compileall -q backtesting strategy data_pipeline agents/agent_0 docs/tests
python signal_pipeline.py --self-check
python risk_pipeline.py --self-check
python -m backtesting --self-check
```

The full suite imports `ib_insync` to prove the pinned class is available, but
tests replace broker behavior with fakes and socket guards. Importing the class
does not connect to IBKR.

### Architecture at a glance

```text
public/captured sources
        |
        v
data_pipeline/historical_data -> canonical CSV partitions -> schema validation
                                                       |
                          no CSV -> MarketSnapshot adapter

strategy/*.py (pure records, equations, signals, sizing, risk, portfolio)
        |
        +--------------------------+
        |                          |
        v                          v
backtesting/*.py              shared paper strategy adapter not implemented
synthetic causal replay
        |
        v
validated backtest CSVs

IBKR paper session -> IbkrPaperRecorder -> validated paper CSVs
IBKR paper session <- Agent 0 random policy/manual operator

maintained historical backtest flow:
historical_data_builder -> signal_pipeline -> risk_pipeline
    -> backtesting.historical -> backtesting.engine -> backtesting.reports
    -> data/results/backtests/<run-id>/{manifest,daily,decisions,orders,fills,trades,positions,summary}.csv
```

## System reference

### Directory and component ownership

| Path                                       | Simplified responsibility                                                |
| ------------------------------------------ | ------------------------------------------------------------------------ |
| `strategy/models.py`                       | Defines shared data structures used across the strategy                  |
| `strategy/spread.py`                       | Calculates spreads, trading costs, and hedge amounts                     |
| `strategy/signal_generation.py`            | Calculates trading signals and tracks position changes                   |
| `strategy/position_sizing.py`              | Determines how large positions should be and how many contracts to trade |
| `strategy/risk_signals.py`                 | Checks risk limits and decides whether trading is allowed                |
| `strategy/costs.py`                        | Estimates trading costs                                                  |
| `strategy/portfolio.py`                    | Selects trades for the portfolio while enforcing risk limits             |
| `data_pipeline/contracts.py`               | Defines and validates required data formats                              |
| `data_pipeline/historical_data/`           | Downloads, cleans, and organizes historical market data                  |
| `data_pipeline/live_data_pipeline/`        | Records IBKR paper quotes, orders, fills, and positions                  |
| `backtesting/engine.py`                    | Sole simulation and accounting engine                                    |
| `backtesting/historical.py`                | Adapts existing historical signal/risk output into causal replay events and writes canonical results. |
| `backtesting/__main__.py`                  | Provides the single `python -m backtesting` CLI and offline self-check. |
| `agents/agent_0/`                          | Runs the random weekly paper-trading experiment                          |
| `docs/tests/`                              | Tests that the strategy and data behave as expected                      |
| `docs/verification/`                       | Stores results and evidence from previous verification tests             |


### Canonical historical data formatting and layout

Historical data is organized into the following folders:

* `data/rates/rates_YYYY.csv`: interest-rate data in basis points
* `data/futures/futures_settlements_YYYY.csv`: futures settlement prices
* `data/contract_risk/contracts.csv`: contract information and effective dates
* `data/contract_risk/contract_risk_YYYY.csv`: contract DV01 and interest-rate sensitivity
* `data/market/daily_market_YYYY.csv`: daily market data

`historical_data_builder.py` downloads historical data from sources such as the US Treasury, New York Fed, Eris, and Treasury-futures data providers.

`canonicalize.py` cleans and converts this data into the standard format used throughout the project.

Downloading the data and formatting it are kept separate. This allows tests to use saved sample data without having to download new data.

### Strategy Files

1. `MarketSnapshot` collects the market, contract, position, and order information available when a trading decision is made.
2. `strategy.spread` calculates the swap/Treasury spread and estimated trading costs.
3. `strategy.signal_generation` decides whether to enter, exit, reverse, or hold a position based on available market data.
4. `strategy.position_sizing` determines how many swap and Treasury contracts should be traded while staying within position and risk limits.
5. `strategy.portfolio` selects which trades can be included in the portfolio without exceeding overall DV01 limits.
6. `strategy.risk_signals` checks risk conditions and decides whether trading should be allowed, reduced, blocked, or positions flattened.
7. The backtest or paper-trading system converts approved `OrderIntent` records into simulated trades or IBKR paper orders.

If required data is invalid, missing, or stale, the strategy will not create a new trade. Risk limits such as DV01, available capacity, losses, drawdowns, margin, broker connection, reconciliation, and contract-roll restrictions can also block trading. This applies primarily for back-testing but will still occur for live.

### Historical backtest flow and outputs

The one maintained historical backtest command is:

```powershell
python -m backtesting --start auto --end auto
```

`backtesting.historical.run_historical_backtest` loads existing historical
signal/risk output, adapts it into typed `ReplayEvent` values, runs the sole
simulation/accounting engine, and writes canonical results. `--refresh-signals`
is the only flag that rebuilds upstream signal/risk data. `--run-id`,
`--output-root`, `--initial-equity`, and the cost flags make a run explicit;
`--self-check` is offline.

For each event, `backtesting.engine.run_backtest`:

1. updates the value of existing positions;
2. applies financing costs;
3. processes orders that were created earlier;
4. handles fills, partial fills, rejected orders, and expired orders;
5. runs the strategy using the current positions and active orders;
6. saves any new orders to be processed later; and
7. records P&L, positions, and other accounting information.

A decision at event `t` cannot fill until a later event. P&L at each event uses
only the position already held while the mark changed, before newly eligible
orders are processed. A requested `start`/`end` window is always `start_flat`:
it begins with the selected initial equity and no inherited position or P&L.

When the strategy reverses direction, the existing position is closed first and the new position is opened based on the amount that was actually filled. This keeps P&L from the old and new positions separate.

`write_results(result, output_root)` saves each backtest run in its own folder containing:

* `manifest.csv`
* `daily.csv`
* `decisions.csv`
* `orders.csv`
* `fills.csv`
* `trades.csv`
* `positions.csv`
* `summary.csv`

The output files are checked against the required backtest data formats before being saved.

`manifest.csv` stores information about the backtest itself, including:

* configuration and schema versions
* backtest date range
* number of rows produced
* input and output hashes
* assumptions used
* any missing data

If market data is missing while a position is open, the backtest uses the data
that is still available and records exactly what was missing. Contract rolls
retain the previous mark with the explicit
`last_pre_roll_mark_zero_return` research proxy so a causal retirement order
can fill without fabricating a cross-contract return. Runs with missing required
data or this research proxy should be treated as diagnostic results rather than
complete executable-performance evidence.

The main backtest assumptions and example tests are stored in `docs/tests/test_naive_backtest.py`.


### Paper-data lifecycle

`IbkrPaperRecorder` receives an already connected, injected broker object. It
validates exact local paper settings, connection state, and managed account
before requesting quotes or recording events. Its public responsibilities are
session validation, quote requests, and quote/order/fill/position recording.
It has no connect, place, or cancel method.

`PaperEventStore` writes only approved paper schemas beneath
`data/paper/agent_N/run_id/`. It rejects unsafe path identifiers, account-like
values, credentials/endpoints, duplicate conflicts, invalid order evolution,
bad types, and noncanonical ordering. It validates a temporary sibling and
atomically replaces the destination.

Broker-derived objects are untrusted. Normalization errors cross the boundary
as generic `PaperSafetyError` messages so credentials, endpoints, client IDs,
and account data are not retained in exception chains.

### Agent 0 paper execution lifecycle

Agent 0 is a deliberately separate random paper experiment:

Agent 0 does not use any signals, it trades completely randomly.

The operator-only entry points are documented in
[`agents/agent_0/SETTINGS.md`](../agents/agent_0/SETTINGS.md). They can submit or
globally cancel paper orders and are not part of setup or test verification.

## Testing and failure handling

The main offline tests are in `docs/tests` and `agents/agent_0/tests`.

Important test modules include:

- `test_strategy_equation_examples` — checks approved strategy calculations and sign conventions.
- `test_schema_contracts` — checks CSV headers, types, keys, ordering, and validation rules.
- `test_ibkr_paper_recorder` — checks paper-session safety, privacy, broker-data handling, and storage.
- `test_naive_backtest` — checks replay timing, accounting, reversals, reports, and manifests.
- `test_characterization` — checks Agent 0 planning, reconciliation, margin checks, routing, and local ledgers.


## Technical reference

### Equations, units, signs, and timing

The full strategy equations and assumptions live in the project contracts and strategy documentation. The `strategy/` package contains the typed calculation logic used by the newer strategy implementation.

The main conventions are:

- **Rates and spreads:** basis points (`_bps`)
- **Raw rates:** decimals (`_decimal`), converted to basis points at the input boundary
- **Prices:** exchange price points (`_price`)
- **DV01:** USD gained or lost for a 1 bp rate increase (`_dv01_usd_per_bp`)
- **P&L and costs:** USD (`_usd`)
- **Event timestamps:** timezone-aware UTC (`_utc`)
- **Daily historical data:** ISO dates
- **Contract quantity:** positive = long contract, negative = short contract

For a position held in the same contract:

`P&L = quantity × price multiplier × price change`

Basket P&L adds the P&L from each leg and subtracts transaction, financing, and roll costs. Price changes between two different contracts are **not** treated as normal same-contract P&L.

DV01 measures interest-rate exposure:

- **Net DV01** is the directional rate exposure after combining positions.
- **Gross DV01** is the total absolute rate exposure.

Hedge quantities are chosen to keep residual DV01 as small as possible while remaining within approved contract, liquidity, and portfolio limits.

Economic direction and exchange order side are separate concepts. A strategy direction may require a combination of long and short futures positions, so sign behavior should remain covered by the strategy equation tests.

Timing is also important:

- a decision at time `t` can only use information available by `t`;
- positions already held earn the price movement into the next event;
- an order cannot fill before it becomes eligible;
- reversing a position means closing the old direction and opening the new one;
- both sides of a reversal create turnover and costs;
- contract rolls include both the closing and opening cost.

### Configuration and dependencies

| Item | Current setting |
| --- | --- |
| Python | 3.12 |
| NumPy | 2.3.5 |
| pandas | 3.0.1 |
| `ib_insync` | 0.9.86 |
| Canonical schema version | 1.0.0 |
| Strategy equation version | `p10.strategy-equations.v1` |
| Position-sizing/risk version | `p33.position-sizing-risk.v1` |
| Agent 0 | Local IBKR paper session, port 7497, client ID 30 |

The strategy version is defined in `strategy/spread.py`, while the sizing version is defined in `strategy/position_sizing.py`.

The root `config.py` belongs to the legacy DataFrame research pipeline. It contains research paths, source settings, maturity mappings, historical-data parameters, sizing constants, and risk limits.

Agent 0 has its own configuration and additional paper-only safeguards. Do not copy account IDs, passwords, credentials, or other secrets into general configuration files or documentation.

### IBKR and `ib_insync`

Agent 0 expects TWS or IB Gateway to already be open and authenticated before it connects.

The project requires a paper connection and validates the configured session before broker activity.

The paper recorder is separate from order execution. `IbkrPaperRecorder` receives an already connected broker object and is responsible for validating the session, requesting quotes, and recording paper events. It does **not** establish the IBKR connection or submit/cancel orders.

Agent 0 uses broker functionality for tasks such as:

- qualifying futures contracts;
- checking existing open orders;
- previewing margin requirements;
- submitting approved paper orders; and
- cancelling orders when explicitly requested.

`ib_insync` is the pinned Python adapter used to interact with the IBKR API. Changes to this dependency should be treated as compatibility changes rather than routine upgrades.

The project's own tests and paper-trading guards are the immediate runtime safety layer. IBKR documentation remains authoritative for TWS and socket-API behavior.

### Known limitations

The current project still has several important limitations:

- The historical adapter currently consumes the existing CSV signal/risk output;
  canonical CSV-to-shared-strategy adaptation remains incomplete.
- Realistic executable contract-roll and liquidity calibration remain incomplete.


### Glossary

| Term | Meaning |
| --- | --- |
| **Basis point (bp)** | 0.01 percentage points |
| **DV01** | Dollar change in value from a 1 bp change in rates |
| **Fixed swap spread** | Swap rate minus the matching Treasury rate |
| **Funding spread** | Floating funding rate minus repo/funding cost |
| **Gross excess spread** | Swap spread minus expected funding burden |
| **Net opportunity** | Expected opportunity after estimated costs |
| **Traditional direction** | Receive-fixed / short-Treasury economic direction (`+1`) |
| **Reverse direction** | Opposite economic direction (`-1`) |
| **Decision timestamp** | Earliest time when all inputs used by a decision were available |
| **`MarketSnapshot`** | Typed collection of market information available at a decision time |
| **`OrderIntent`** | Broker-independent description of a paper order the strategy wants submitted |
| **Canonical data** | Data converted into the project's approved, validated schemas |
| **Synthetic mechanics** | Artificial test data used to prove that the engine behaves correctly |
| **Agent 0** | Random IBKR paper-order experiment; not the swap-arbitrage strategy |
| **Paper recorder** | IBKR adapter that validates and records paper events but does not manage the connection or submit orders |
| **Lifecycle trade** | A trade tracked from opening exposure through final closure |
| **Fail closed** | Refuse new risk or refuse to overwrite output when required validation fails |

## Project capabilities and operating steps

### 1. Prepare and verify the Python environment

From the repository root, create a Python 3.12 environment (or recreate it if
its base interpreter has been removed), install the pinned dependencies, and
run the offline checks:

```powershell
& "C:\Path\To\Python312\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip check
python signal_pipeline.py --self-check
python risk_pipeline.py --self-check
python -m backtesting --self-check
```

The self-checks validate deterministic examples only; they do not download data,
connect to IBKR, or establish strategy profitability.

### 2. Re-collect and prepare historical research data

Use the legacy research pipeline to refresh the public rate and Eris sources,
rebuild the derived raw-data files, and calculate risk-sized signals:

```powershell
python risk_pipeline.py --refresh-raw --treasury --eris
```

This produces the derived files consumed by the legacy backtest, including
`data/raw_data/risk_data.csv`, swap settlement/DV01 data, and Treasury-futures
data. It requires network access and its results depend on public-source
availability, coverage, and revisions. To refresh only the signal/risk layer
from existing raw data, run:

```powershell
python risk_pipeline.py --refresh-signals
```

### 3. Run the historical backtest

After the derived historical input exists, run the canonical causal replay:

```powershell
python -m backtesting --start auto --end auto
```

Each run writes `manifest.csv`, `daily.csv`, `decisions.csv`, `orders.csv`,
`fills.csv`, `trades.csv`, `positions.csv`, and `summary.csv` under
`data/results/backtests/<run-id>/`. Use `--refresh-signals` only when the
upstream signal/risk data needs rebuilding. The replay models delayed fills,
costs, financing, and roll handling, but its current data adapter and roll/
liquidity calibration remain research limitations.

### 4. Run Agent 0's paper-only weekly order experiment

Agent 0 is separate from the swap-arbitrage strategy: it creates a constrained,
random weekly paper-order plan. Before running it, a human must complete the
paper-session checklist in `agents/agent_0/SETTINGS.md`, start TWS or IB Gateway
in paper mode, and set the approved paper account only in the local environment:

```powershell
$env:AGENT0_IBKR_ACCOUNT = "<your-approved-paper-account>"
.venv\Scripts\python.exe agents\agent_0\run.py
```

The runner validates paper-only connectivity, account visibility, contract
qualification, working-order limits, and a margin reserve before submitting any
paper orders. To cancel all working orders visible in that paper session,
including manual and other API-client orders, use:

```powershell
.venv\Scripts\python.exe agents\agent_0\run.py --cancel-all
```

Cancellation is intentionally broad and resets local upcoming orders to planned;
it does not create a replacement weekly plan. Never use a live account, live
port, or account value stored in source control.
