# P21 Data Migration Preview

## Safety contract

This is a preview only. P21 performed no move, copy, rewrite, archive,
supersession, deletion, canonicalization, staging run, or external read. The
tested rules in `data_pipeline/contracts.py` classify all 1,487 P20-audited
CSV artifacts exactly once. Every original stays authoritative and recoverable
until P24 staging passes and the user approves the applicable gate. Deletion
requires the later consolidation prompt; no rule below schedules it.

## Complete mapping

| Rule | Matched P20 artifacts | Future action | Staged destination | Before -> after expectation | Prerequisite and recovery |
|---|---:|---|---|---|---|
| `eris_vendor_cache` | 1,474 `data/cache/eris_sofr_settlements_v3/*.csv` files | keep immutable source | `data/source/futures/futures_settlements_YYYY.csv` | Each 64-column vendor file remains byte-identical. Approved 2Y/5Y IDs match `^(?:YIT\|YIW)[HMUZ]\d{2}$`. Parse the observation date only from `^Eris_Instruments_(\d{8})_Settles\.csv$` as `YYYYMMDD`; `EvaluationDate` must equal it. Block the complete file on duplicate `(EvaluationDate,Symbol)`. Per file, emitted keys equal exactly the matching unique source keys and emitted count equals that key-set size. | Validate both vendor schema variants; recover from untouched cache plus P20 SHA-256 |
| `cme_swap_master` | 1 `data/cme_swap_data.csv` | regenerate | `data/source/futures/futures_settlements_YYYY.csv` and `contract_risk_YYYY.csv` | 2,948 rows split by year; 4 columns map to settlement/risk contracts without silent row loss | Reconcile year totals, dates, prices, DV01, and tickers; retain current file |
| `treasury_futures_master` | 1 `data/treasury_futures_data.csv` | regenerate | `data/source/futures/futures_settlements_YYYY.csv` and `contract_risk_YYYY.csv` | 2,942 rows split by year; 4 columns retain explicit proxy lineage | Validate contract identity and DV01 method; retain current file |
| `swap_rates` | 1 `data/swap_rates.csv` | regenerate | `daily_market_YYYY.csv` | 1,474 dates expand from 5 wide columns to long consumed price rows; returns are later features | Reconcile every nonmissing price; retain current file |
| `treasury_futures` | 1 `data/treasury_futures.csv` | regenerate | `daily_market_YYYY.csv` | 1,471 dates expand from 5 wide columns to long proxy price rows; returns are later features | Preserve proxy labels and reconcile prices; retain current file |
| `treasury_rates` | 1 `data/treasury_rates.csv` | regenerate | `data/source/rates/rates_YYYY.csv` and `daily_market_YYYY.csv` | 2,143 dates expand from 16 wide columns to one long row per present consumed series | Validate units/publication clocks and reconcile spot values; retain current file |
| `raw_wide` | 1 `data/raw_price_data.csv` | supersede after validation | `daily_market_YYYY.csv` | 2,154 keyed dates reconstructed from narrow market rows instead of 24 copied columns | Canonical staging and consumer parity must pass; restore/continue current file |
| `signal_wide` | 1 `data/signal_data.csv` | supersede after validation | run `decisions.csv` | 1,471 proxy dates remain reproducible as legacy evidence; 40 columns become market inputs plus narrow decisions | Shared strategy parity must pass; restore/continue current file |
| `risk_wide` | 1 `data/risk_data.csv` | supersede after validation | run `decisions.csv` and `positions.csv` | 1,471 proxy dates remain reproducible; 72 columns become narrow risk decisions and positions | Risk/sizing parity must pass; restore/continue current file |
| `legacy_backtests` | 4 `data/swap_arb_backtest_*.csv` files | archive labelled legacy | `data/results/legacy_proxy/<original_filename>` | Preserve exact respective 2,148, 1,471, 756, and 753 rows and original 85/99-column shapes | Replacement reports and hashes must reconcile; recover by moving exact named file back |
| `r2_inventory` | 1 `r2_objects.csv` | keep immutable source | `r2_objects.csv` in place; excluded from canonical manifests | Preserve all 2,117 rows and 9 metadata columns; never use as market input | Validate its unchanged inventory hash if later reviewed; recover from untouched manifest |

Coverage total: `1,474 + 13 = 1,487` artifacts. The executable coverage test
rejects an unknown path and rejects zero or multiple matching rules.

## Staging order after MG3

1. P22 reads only approved source objects and writes new source/canonical
   partitions beside manifests.
2. P23 proves paper telemetry schemas with fake brokers only.
3. P24 stages every approved conversion under a new repository-local staging
   directory and compares hashes, row counts, date ranges, keys, and spot
   values.
4. The user reviews staging at MG4. Approval can authorize recoverable writes
   and moves listed here; it never authorizes deletion of caches or legacy
   results.

## Retention decision

Raw vendor caches are retained indefinitely as immutable source evidence until
an explicit later cleanup decision records an equivalent recoverable copy,
verified hashes, all consumers migrated, and a recovery method. R2 inventory
metadata is retained but is not canonical market data. Wide derived pipeline
files and legacy proxy backtests remain in place until their named replacement
passes validation; archival preserves original names and bytes.

## Before/after interpretation

Row counts for long canonical tables are not expected to equal the source file
row count: one wide date may emit multiple series/instrument observations, and
only present, approved consumer fields are emitted. P24 must calculate the
expected count from nonmissing approved fields and reconcile it exactly. No
missing value is coerced, no unavailable exact field receives a proxy, and no
current return/feature/result column is copied into canonical market input.
