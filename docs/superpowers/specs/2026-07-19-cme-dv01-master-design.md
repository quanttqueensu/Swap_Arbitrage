# CME Strategy DV01 Master Design

## Goal

Make `data/cme_swap_data.csv` the only derived CSV that stores DV01 while
keeping the strategy, risk sizing, live-agent sizing, and backtest functional.

## Output contract

`data/cme_swap_data.csv` is a long, date-sorted file with exactly four columns:

```csv
date,ticker,price,dv01
```

Each row is the active 2Y or 5Y SOFR swap-futures contract selected by the
existing strategy logic for one settlement date. `ticker` is the full Eris
contract symbol. `price` is `FinalSettlementPrice`, and `dv01` is the source
contract DV01.

The pair `(date, ticker)` must be unique. Dates use ISO `YYYY-MM-DD` format;
price and DV01 are numeric. Rows missing any required value are invalid and do
not enter the master file.

## Source lineage

Public Eris settlement files are the sole research source for the YIT 2Y and
YIW 5Y swap-futures price and DV01 histories. A pull reads or downloads each
daily source file, applies the existing SOFR and active-contract selection, and
builds the four-column master in the same pass.

The optional historical IBKR pull is removed from the research pipeline. It
currently duplicates YIT/YIW from a second source, and none of its swap or
Treasury-futures columns are consumed by signal generation, risk sizing,
backtesting, or live-agent sizing. Removing it also removes the unused
`ibkr_market_data.csv` path and the research CLI flags that trigger it.

Agent 0's separate IBKR connection and execution code remains unchanged.
Treasury XML yields and NY Fed SOFR/EFFR remain independent inputs because they
are different instruments or benchmarks, not duplicate swap-futures histories.

Downloaded Eris settlement files under `data/cache/` remain immutable raw
inputs so the master can be rebuilt without re-downloading 1,474 files.

## Pull and derived-price flow

The active-contract extractor retains the selected contract ticker along with
its price and DV01. The pull produces two views from those selected records:

- `cme_swap_data.csv`: `date,ticker,price,dv01`.
- `swap_rates.csv`: the existing wide strategy price and return columns, with
  no DV01 columns.

`raw_price_data.csv` merges Treasury and funding rates with price-only swap
data. It contains no DV01 column. Re-running `python raw_price_data.py --eris`
rebuilds both swap files from cached or newly downloaded settlements and then
rebuilds `raw_price_data.csv`.

## Signal flow

Signal calculations remain price-based. `signal_data.py` reads
`raw_price_data.csv`, calculates residuals, z-scores, and positions, and writes
`signal_data.csv` without any DV01 column.

## Risk flow

`risk_data.py` reads signal data and `cme_swap_data.csv` separately. It maps
full tickers to maturity using the configured YIT/YIW product roots, joins on
the exact date, and uses master DV01 values only in memory to calculate:

- swap-futures contract quantities;
- estimated swap notional;
- Treasury-futures hedge quantities;
- gross and net exposure limits; and
- risk blocking decisions.

Missing, nonnumeric, or nonpositive DV01 blocks the affected active maturity;
the code does not forward-fill DV01 across a missing settlement or contract
roll. The saved `risk_data.csv` retains contract quantities, notionals,
directions, and risk flags, but drops every column whose name contains `dv01`.
This keeps Agent 0 compatible because its sizing code consumes rounded contract
quantity columns and only falls back to notional when those are absent.

## Backtest flow

The backtest reads `risk_data.csv` and `cme_swap_data.csv`. It reconstructs the
temporary exposure needed for P&L by date and maturity. Swap P&L continues to
use prior swap notional and price return. Treasury P&L uses the prior rounded
Treasury contract quantity multiplied by the configured Treasury-futures DV01
and the Treasury-yield change.

Exposure summaries may be calculated in memory, but saved backtest CSVs contain
no DV01 columns. Existing backtest CSVs are rewritten once to remove every
DV01-named column.

## Derived-file cleanup

After the new pipeline is verified, every existing `data/*.csv` except
`cme_swap_data.csv` is checked and rewritten without columns whose names contain
`dv01`, case-insensitively. The cleanup includes `swap_rates.csv`,
`raw_price_data.csv`, `signal_data.csv`, `risk_data.csv`, and historical
backtest outputs. Raw cached settlement files are excluded.

Future writers enforce the same rule before saving their derived CSVs.

## Verification

Tests and self-checks cover:

- exact master schema, numeric values, ISO dates, sort order, and uniqueness;
- active YIT/YIW selection and ticker preservation across a contract roll;
- one research source for YIT/YIW and removal of the unused IBKR history path;
- exact-date risk joins and blocking when master DV01 is invalid or missing;
- unchanged rounded swap and Treasury contract sizing for known inputs;
- backtest P&L using contract quantities and transient exposure; and
- zero DV01-named columns in every derived CSV except the master.

The final verification rebuilds the data chain from cached inputs, runs all
self-checks and tests, runs a representative backtest, scans derived CSV
headers, and confirms that a subsequent Eris pull still updates both swap
outputs.

## Non-goals

- Do not alter or strip the downloaded source settlement files.
- Do not change signal formulas, thresholds, or the traded 2Y/5Y universe.
- Do not change Agent 0 order generation, IBKR connection, or execution logic.
- Do not add a new dependency or storage format.
