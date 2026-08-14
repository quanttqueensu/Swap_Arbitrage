# Backtesting Consolidation Design

## Goal

Make `backtesting/` the repository's only backtest implementation and public
entry point. Preserve the working historical signal and risk preparation,
route historical simulations through the causal replay engine, emit only the
validated canonical report set, and remove the root `backtest_engine.py` after
equivalence and regression checks pass.

The maintained command becomes:

```powershell
python -m backtesting --start YYYY-MM-DD --end YYYY-MM-DD
```

## Current state and reason for consolidation

The root `backtest_engine.py` predates the typed replay package. It loads the
DataFrame output of `signal_pipeline.py` and `risk_pipeline.py`, merges market
masters, calculates prior-position P&L, and writes one wide derived CSV. The
newer `backtesting/` package separately models causal decisions, delayed and
partial fills, positions, trade lifecycles, costs, equity, drawdown, and eight
validated canonical artifacts.

The package is tested but has no historical-data adapter or CLI, so it cannot
currently replace the root runner. Keeping both indefinitely creates two
definitions of execution, accounting, and output. Consolidation removes that
duplication; it does not add a third compatibility layer.

## Scope

The migration will:

- add a historical adapter inside `backtesting/` that consumes the existing
  signal/risk and market-master outputs;
- translate each valid dated row into a typed `ReplayEvent` and translate
  desired contract changes into existing Phase 4 decision, risk, and order
  records;
- run all simulated execution, costs, position state, P&L, equity, drawdown,
  and trade lifecycle accounting through `backtesting.run_backtest`;
- expose one `python -m backtesting` command;
- write only the canonical run directory produced by `write_results`;
- migrate the root engine's maintained behavioral tests to the package;
- remove `backtest_engine.py` after the replacement passes its checks; and
- update `README.md`, `docs/TECHNICAL_DOCUMENTATION.md`, `docs/FILE_MAP.md`,
  and `docs/FUNCTION_INVENTORY.md` to describe only the consolidated path.

The migration will not rewrite the signal or risk equations, add a permanent
root wrapper, preserve the legacy wide result CSV, download data by default,
or claim that the still-missing canonical CSV-to-shared-strategy adapter and
realistic executable-market calibration have been completed.

## Architecture and component boundaries

### Existing upstream preparation

`signal_pipeline.py` and `risk_pipeline.py` remain responsible for producing
dated desired positions, rounded swap and Treasury contract quantities, risk
allow/block state, and the market-master joins they already own. Their
equations are reused in place and are not copied into `backtesting/`.

This is an explicit transitional boundary: it preserves the working
historical strategy input while removing duplicate backtest execution and
accounting. A future canonical-data-to-shared-strategy adapter can replace
this input boundary without changing the replay engine or report writer.

### Historical adapter

Add one focused module, `backtesting/historical.py`. Its public operation is
`run_historical_backtest`; small conversion helpers remain private. It will:

1. load or refresh the existing signal/risk frame;
2. merge the required swap and Treasury ticker, price, DV01, and multiplier
   data;
3. validate unique ordered dates and required active-contract fields;
4. build one UTC `MarketSnapshot` and `ReplayEvent` per date;
5. derive typed decisions and desired order changes from the current row's
   risk-approved target quantities and the positions supplied by the replay
   engine; and
6. call `run_backtest` and `write_results`.

The adapter owns format conversion only. It must not calculate an alternate
P&L, maintain a second position ledger, or reproduce strategy equations.

### Command-line entry point

Add `backtesting/__main__.py` with the arguments required by the current
workflow: start date, end date, initial equity, refresh-signals, output root,
and an optional run identifier. Start and end default to `auto`, preserving
the first/last available-date behavior. The command also exposes the five
existing `NaiveAssumptions` values directly: bid/ask half-spread points,
commission USD per contract, slippage points, financing USD per contract-day,
and roll USD per contract. It adds no second cost model.

The command prints the run directory and a concise summary derived from the
canonical result. It does not generate the legacy wide CSV.

### Replay and reporting

`backtesting.engine` remains the sole owner of order timing, fills, position
state, financing, transaction costs, realized and unrealized P&L, equity, and
drawdown. `backtesting.reports` remains the sole owner of report rendering,
schema validation, deterministic ordering, hashing, and atomic replacement.

Every successful historical run contains exactly:

- `manifest.csv`
- `daily.csv`
- `decisions.csv`
- `orders.csv`
- `fills.csv`
- `trades.csv`
- `positions.csv`
- `summary.csv`

## Data and timing semantics

Historical dates are converted to deterministic UTC decision timestamps.
Observations must be available no later than their decision timestamp. A
target created on one event cannot fill until a later eligible event, matching
the replay engine's causal contract and the root engine's prior-position P&L
principle.

The adapter computes orders as the difference between desired contract
quantities and replay-supplied current positions. Risk-blocked rows create no
new exposure; any required flattening follows the existing risk output rather
than an adapter-specific policy. Ticker changes are represented as close and
open orders so the replay engine owns roll turnover and costs.

Date windows use the replay engine's `start_flat` policy. A requested window
does not inherit positions established before its first included event.

## Validation and failure behavior

The historical adapter fails closed before the replay when active quantities
have a missing ticker, nonpositive or missing price, nonpositive or missing
DV01, invalid multiplier, duplicate date, or duplicate instrument. Empty or
reversed date windows are errors.

Missing marks for positions already held use the replay engine's existing
diagnostic partial-accounting policy and are recorded in the manifest. The
adapter does not silently forward-fill marks, DV01, tickers, or contract
metadata.

Default runs use local files only. `--refresh-signals` retains the existing
explicit refresh behavior. Reports are exposed only after every temporary CSV
passes its canonical schema validation.

## Compatibility and removal policy

Output compatibility is behavioral, not byte-for-byte compatibility with the
legacy 99-column CSV. A frozen zero-cost fixture must demonstrate equivalent
dates, desired quantities, risk-block behavior, gross daily P&L, ending equity,
and active-range behavior between the legacy calculations and the new
historical adapter. New canonical reports remain the only supported output.

The root file is removed in the same migration only after those equivalence
checks and all focused replay/report tests pass. No permanent shim or alias is
kept. Maintained imports, test commands, and documentation must contain no
reference to `backtest_engine.py`; historical audit and verification records
may retain factual references to past repository states.

## Tests and verification

Add one focused `docs/tests/test_historical_backtest.py` module. Migrate the
maintained cases from `BacktestMasterTests` and cover:

- historical row-to-event conversion;
- UTC availability and unique-date validation;
- delayed fills and prior-position P&L;
- risk-blocked entry prevention and flattening behavior;
- missing/nonpositive active-market field rejection;
- ticker-roll close/open behavior;
- date-window start-flat behavior;
- zero-cost behavioral equivalence with the legacy fixture;
- canonical report generation from historical inputs; and
- the exact `python -m backtesting --self-check` offline smoke path.

Update the import-smoke test to import the package entry point rather than the
deleted root module. Retain the existing 16 replay/report tests. Final
verification runs the focused historical and naive-backtest suites, all
`docs/tests`, Agent 0 tests in an environment with declared dependencies,
`compileall` over maintained Python packages, signal and risk self-checks, the
new backtesting self-check, and `git diff --check`.

## Documentation deliverables

After implementation:

- `README.md` shows the single supported backtest command and output location.
- `docs/TECHNICAL_DOCUMENTATION.md` replaces the two-path diagrams and
  commands with the consolidated data flow, explicitly retaining the current
  historical-input limitation.
- `docs/FILE_MAP.md` removes the root engine and describes
  `backtesting/historical.py` and `backtesting/__main__.py`.
- `docs/FUNCTION_INVENTORY.md` removes the legacy functions and lists every
  new public historical adapter and CLI-facing function with accurate inputs
  and outputs.

Documentation is updated only after the code and tests establish the final
public surface, so names and commands are derived from implemented behavior.

## Success criteria

The repository has one maintained backtest command, one execution/accounting
engine, one canonical report format, no root `backtest_engine.py`, no
maintained test or documentation dependency on it, and passing focused and
full verification. The manifest continues to label the evidence truthfully;
consolidation does not upgrade synthetic or incomplete market-data evidence
into a profitability or production-readiness claim.
