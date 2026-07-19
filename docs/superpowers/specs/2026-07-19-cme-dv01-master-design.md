# CME Strategy DV01 Master Design

## Goal

Keep DV01 in two canonical market masters while keeping every derived strategy,
risk, and backtest CSV free of DV01:

- `data/cme_swap_data.csv` for Eris YIT/YIW contracts; and
- `data/treasury_futures_data.csv` for paired ZT/ZF research data.

## Output contract

Both masters are long, date-sorted files with exactly four columns:

```csv
date,ticker,price,dv01
```

In the Eris master, each row is the active 2Y or 5Y SOFR swap-futures contract
selected by the strategy for one settlement date. `ticker` is the full Eris
contract symbol, `price` is `FinalSettlementPrice`, and `dv01` is the source
contract DV01.

With the public pull, Treasury `ticker` is the continuous vendor symbol `ZT=F`
or `ZF=F`, and `price` is its daily close. Its `dv01` is deliberately a
strategy-specific research proxy: same-date paired Eris DV01 multiplied by
CME's fixed Eris/Treasury inter-commodity-spread ratio (YIT:ZT 2:1 and YIW:ZF
1:1). It must not be described as actual CTD-derived Treasury-futures DV01.
With a licensed normalized CTD input, `ticker` is the full contract symbol and
`dv01` is calculated from the supplied CTD cash DV01 and conversion factor.

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

The public Treasury-futures source is used only once, for ZT/ZF continuous
prices. DGS2/DGS5 remain macro context, not a second futures-price source.

### Treasury production-data boundary

Actual Treasury-futures DV01 is the cheapest-to-deliver cash security's DV01
divided by its conversion factor, using $200,000 face for ZT and $100,000 for
ZF. Both the CTD and its conversion factor can change, so a fixed number or a
fixed Eris spread ratio cannot validate exact daily futures DV01.

Authoritative historical settlement, deliverable-basket, CTD, and conversion-
factor data requires an entitled CME DataMine or other licensed feed. The
public pull remains usable for reproducible research and CME's executable
fixed-ratio spread structure, but the command prints a warning and the code
names the method `cme_fixed_ics_ratio_proxy`. A production trading deployment
must replace that proxy at the Treasury-master builder boundary with licensed
contract-month settlement and CTD-derived DV01 data.

The selected Eris contract is based on the existing DV01-band continuity
heuristic, while the public Treasury input is an anonymous continuous root.
Same-date maturity pairing therefore does not prove that the two records are an
executable same-month ETU/EWV pair. Production use also needs an approved
full-ticker roll and pairing map.

The implementation accepts that production boundary directly:

```powershell
python raw_price_data.py --treasury-futures-ctd-file data/cache/treasury_ctd.csv
```

The normalized licensed input columns are
`date,ticker,price,ctd_cash_dv01_per_100k,conversion_factor`. The builder uses
`ctd_cash_dv01_per_100k * face_value / 100000 / conversion_factor`, validates
positive exact inputs, and writes the same four-column Treasury master. Full
contract ticker changes then drive roll back-adjustment, zero cross-contract
P&L, and close/open turnover automatically. Acquisition of the licensed raw
file is through CME DataMine's authenticated API or another licensed vendor;
credentials and an entitlement are intentionally not embedded in this repo.

Downloaded Eris settlement files under `data/cache/` remain immutable raw
inputs so the master can be rebuilt without re-downloading 1,474 files.

## Pull and derived-price flow

The active-contract extractor retains the selected contract ticker along with
its price and DV01. The pull produces two views from those selected records:

- `cme_swap_data.csv`: `date,ticker,price,dv01`.
- `swap_rates.csv`: the existing wide strategy price and return columns, with
  no DV01 columns.

The master keeps each contract's unadjusted settlement price. The derived
strategy price series is additively back-adjusted when the selected ticker
changes, and the cross-contract return is set to zero, so a roll cannot create
a false price signal or P&L observation.

`raw_price_data.csv` merges Treasury and funding rates with price-only swap and
Treasury-futures data. It contains no DV01 column. Re-running
`python raw_price_data.py --eris` rebuilds both public masters, both price-only
files, and then `raw_price_data.csv`. `--treasury-futures` refreshes just the
public Treasury master and downstream price data from the existing Eris master;
`--treasury-futures-ctd-file` uses the licensed contract/CTD path instead.

## Signal flow

Signal calculations remain price-based. `signal_data.py` regresses each
back-adjusted Eris price against its paired Treasury-futures price, calculates
residuals, z-scores, and positions, and writes `signal_data.csv` without any
DV01 column. DGS yields remain context columns and are not the traded hedge leg.

This is a relative-value price signal, not a complete theoretical par swap-
spread calculation: converting the Eris settlement formula into a par swap
rate and the CTD futures price into an implied Treasury yield requires licensed
contract and CTD inputs not present in the public research pull.

Signal rows are restricted to the common settlement calendar where all four
traded price series have marks. A missing-market holiday is skipped rather than
resetting an open position or creating a synthetic close; the next marked row
measures the price change across the gap.

## Risk flow

`risk_data.py` reads signal data and both masters separately. It maps full Eris
tickers and continuous Treasury symbols to maturity, joins on the exact date,
and uses master DV01 values only in memory to calculate:

- swap-futures contract quantities;
- estimated swap notional;
- Treasury-futures hedge quantities;
- gross and net exposure limits; and
- risk blocking decisions.

Missing, nonnumeric, or nonpositive DV01 in either leg blocks both contracts for
the affected active maturity. The code does not forward-fill DV01 across a
missing settlement or contract roll. The saved `risk_data.csv` retains contract
quantities, notionals, directions, and risk flags, but drops every column whose
name contains `dv01`.
This keeps Agent 0 compatible because its sizing code consumes rounded contract
quantity columns and only falls back to notional when those are absent.

## Backtest flow

The backtest reads `risk_data.csv` and both masters. It reconstructs temporary
market fields by date and maturity. Each leg's P&L uses prior rounded contracts
times the daily price-point change times the contract point value: $1,000 for
YIT/YIW, $2,000 for ZT, and $1,000 for ZF. It no longer estimates Treasury P&L
from DGS yield changes or static DV01.

Ticker identity is joined transiently during a backtest. A roll charges
close-and-open turnover even when the rounded contract count does not change.
If a nonzero saved contract quantity has no positive exact-date master price,
DV01, or ticker, the backtest fails instead of treating the exposure as zero.
Eris cross-contract price changes are excluded on roll dates and close/open
turnover is charged. The public Treasury symbols are continuous and therefore
cannot expose exact roll dates; this is another production-data limitation.
Public-root backtest returns can include vendor roll discontinuities and are
diagnostic output only, not evidence of strategy profitability.
Date-filtered backtests retain prior-position P&L but rebase equity and drawdown
to the requested initial capital at the start of the selected window.

Exposure summaries may be calculated in memory, but saved backtest CSVs contain
no DV01 columns. Existing backtest CSVs are rewritten once to remove every
DV01-named column.

## Derived-file cleanup

After the new pipeline is verified, every existing `data/*.csv` except the two
masters is checked and rewritten without columns whose names contain `dv01`,
case-insensitively. The cleanup includes `swap_rates.csv`,
`raw_price_data.csv`, `signal_data.csv`, `risk_data.csv`, and historical
backtest outputs. Raw cached settlement files are excluded.

Future writers enforce the same rule before saving their derived CSVs.

## Verification

Tests and self-checks cover:

- exact master schema, numeric values, ISO dates, sort order, and uniqueness;
- active YIT/YIW selection and ticker preservation across a contract roll;
- one research source for YIT/YIW and removal of the unused IBKR history path;
- exact-date risk joins and blocking when master DV01 is invalid or missing;
- rounded swap and Treasury contract sizing for known paired inputs;
- point-value backtest P&L using contract quantities and transient prices;
- exact-date joins without forward filling; and
- zero DV01-named columns in every derived CSV except the two masters.

The final verification rebuilds the data chain from cached inputs, runs all
self-checks and tests, runs a representative backtest, scans derived CSV
headers, and confirms that a subsequent Eris pull still updates both swap
outputs.

## Authoritative references

- CME Eris SOFR Swap Futures FAQ and contract specification:
  <https://www.cmegroup.com/markets/interest-rates/files/eris-sofr-swap-futures-faq.pdf>
- CME Eris/Treasury swap-spread contract ratios and direction:
  <https://www.cmegroup.com/articles/2024/trading-swap-spreads-with-futures-a-primer-for-eristreasury-swap-spreads.html>
- CME Treasury futures CTD/BPV methodology:
  <https://www.cmegroup.com/education/courses/introduction-to-treasuries/how-can-you-measure-risk-in-treasuries>
- CME DataMine authenticated API:
  <https://www.cmegroup.com/datamine/datamine-api.html>

## Non-goals

- Do not alter or strip the downloaded source settlement files.
- Do not change signal thresholds or the traded 2Y/5Y universe.
- Do not change Agent 0 order generation, IBKR connection, or execution logic.
- Do not add a new dependency or storage format.
