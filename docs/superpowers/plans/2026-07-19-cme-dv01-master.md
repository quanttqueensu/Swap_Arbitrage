# CME Strategy DV01 Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one four-column CME strategy master for swap DV01, remove DV01 from every other derived CSV, and keep risk sizing and backtesting functional.

**Architecture:** Public Eris settlements remain the sole YIT/YIW research source and produce a long `cme_swap_data.csv` plus price-only wide strategy data. Risk joins the master on exact dates and uses DV01 transiently; backtesting uses saved contract quantities plus transient master DV01 and saves only non-DV01 outputs.

**Tech Stack:** Python 3, existing pandas/numpy dependencies, standard-library `unittest`, pathlib, cached public Eris CSVs.

## Global Constraints

- `data/cme_swap_data.csv` has exactly `date,ticker,price,dv01`.
- Only selected active 2Y YIT and 5Y YIW SOFR contracts enter the master.
- Public Eris settlement files are the sole YIT/YIW research source.
- Downloaded files under `data/cache/` remain unchanged.
- Every other derived CSV contains no column whose name includes `dv01`, case-insensitively.
- Missing, nonnumeric, or nonpositive master DV01 blocks the affected active maturity without forward-filling.
- Signal formulas, thresholds, the 2Y/5Y universe, and Agent 0 execution behavior do not change.
- No new dependency or storage format is added.

---

### Task 1: CSV guard, CME master, and single-source pull

**Files:**
- Create: `data_io.py`
- Create: `tests/test_dv01_pipeline.py`
- Modify: `config.py`
- Modify: `raw_price_data.py`

**Interfaces:**
- Produces: `without_dv01_columns(df: pd.DataFrame) -> pd.DataFrame`
- Produces: `save_derived_csv(df: pd.DataFrame, path: Path) -> pd.DataFrame`
- Produces: `clean_existing_derived_csvs(data_dir: Path, master_path: Path) -> list[Path]`
- Produces: `build_cme_swap_data(eris: pd.DataFrame) -> pd.DataFrame`
- Produces: `strategy_swap_prices(eris: pd.DataFrame) -> pd.DataFrame`

- [ ] **Step 1: Write failing data-output tests**

Create `tests/test_dv01_pipeline.py` with a `unittest.TestCase` that builds a
two-date wide Eris frame containing `eris_swap_2y_ticker`,
`eris_swap_2y_price`, `eris_swap_2y_dv01`, and the corresponding 5Y columns.
Assert that `build_cme_swap_data` returns exactly:

```python
expected = pd.DataFrame(
    {
        "date": pd.to_datetime(
            ["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"]
        ),
        "ticker": ["YITH24", "YIWH24", "YITM24", "YIWM24"],
        "price": [99.1, 98.1, 99.2, 98.2],
        "dv01": [19.0, 46.0, 19.1, 46.1],
    }
)
```

Also assert that `strategy_swap_prices` contains no DV01 or ticker columns,
and that `clean_existing_derived_csvs` preserves `cme_swap_data.csv` while
removing `target_dv01` from another CSV in a temporary directory.

- [ ] **Step 2: Run the tests to verify RED**

Run:

```powershell
python -m unittest tests.test_dv01_pipeline -v
```

Expected: import failure because `data_io`, `build_cme_swap_data`, and
`strategy_swap_prices` do not exist.

- [ ] **Step 3: Add the minimal CSV guard**

Create `data_io.py` with these implementations:

```python
from pathlib import Path
import pandas as pd


def without_dv01_columns(df: pd.DataFrame) -> pd.DataFrame:
    columns = [column for column in df.columns if "dv01" in column.lower()]
    return df.drop(columns=columns)


def save_derived_csv(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    output = without_dv01_columns(df)
    path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(path, index=False)
    return output


def clean_existing_derived_csvs(data_dir: Path, master_path: Path) -> list[Path]:
    cleaned = []
    for path in sorted(data_dir.glob("*.csv")):
        if path.resolve() == master_path.resolve():
            continue
        columns = pd.read_csv(path, nrows=0).columns
        if any("dv01" in column.lower() for column in columns):
            save_derived_csv(pd.read_csv(path), path)
            cleaned.append(path)
    return cleaned
```

- [ ] **Step 4: Add the master path and ticker-column map**

In `config.py`, add:

```python
CME_SWAP_DATA_FILE = DATA_DIR / "cme_swap_data.csv"

SWAP_TICKER_COLUMNS = {
    "2Y": "eris_swap_2y_ticker",
    "5Y": "eris_swap_5y_ticker",
}
```

Delete the unused `IBKR_MARKET_DATA_FILE`, historical IBKR output-column maps,
bar-history settings, `RATES_WITH_SWAPS_FILE`, `SIGNALS_FILE`,
`MAX_DV01_PER_MATURITY`, and `REQUIRE_ACTUAL_SWAP_DV01`. Preserve connection
settings imported by Agent 0.

- [ ] **Step 5: Preserve selected tickers and build the master**

In `raw_price_data.py`, preserve columns ending in `_ticker` as text inside
`clean_price_frame`. Add the selected `Symbol` to each maturity row inside
`extract_eris_swap_row`, then implement:

```python
def build_cme_swap_data(eris: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for maturity in MATURITIES:
        ticker_col = SWAP_TICKER_COLUMNS.get(maturity)
        price_col = SWAP_COLUMNS.get(maturity)
        dv01_col = SWAP_DV01_COLUMNS.get(maturity)
        if not ticker_col or not price_col or not dv01_col:
            continue
        if not {"date", ticker_col, price_col, dv01_col}.issubset(eris.columns):
            continue
        frames.append(
            eris[["date", ticker_col, price_col, dv01_col]].rename(
                columns={ticker_col: "ticker", price_col: "price", dv01_col: "dv01"}
            )
        )
    if not frames:
        raise RuntimeError("No selected CME swap rows available for the master file.")
    output = pd.concat(frames, ignore_index=True)
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.normalize()
    output["ticker"] = output["ticker"].astype("string").str.strip()
    output["price"] = pd.to_numeric(output["price"], errors="coerce")
    output["dv01"] = pd.to_numeric(output["dv01"], errors="coerce")
    output = output.dropna(subset=["date", "ticker", "price", "dv01"])
    output = output[(output["ticker"] != "") & (output["dv01"] > 0)]
    if output.duplicated(["date", "ticker"]).any():
        raise RuntimeError("Duplicate date/ticker rows in CME swap master data.")
    return output.sort_values(["date", "ticker"]).reset_index(drop=True)


def strategy_swap_prices(eris: pd.DataFrame) -> pd.DataFrame:
    output = without_dv01_columns(eris)
    ticker_columns = [column for column in output if column.endswith("_ticker")]
    return output.drop(columns=ticker_columns)
```

Save the master directly, save `swap_rates.csv` and `raw_price_data.csv`
through `save_derived_csv`, and call `clean_existing_derived_csvs` after the
raw build.

- [ ] **Step 6: Remove the duplicate historical IBKR research path**

Delete `ibkr_tools`, `connect_ibkr`, contract-resolution/history functions,
`get_ibkr_traded_futures_data`, `refresh_ibkr`/`pull_ibkr` parameters, and the
research `--ibkr` CLI flags from `raw_price_data.py`, `signal_data.py`, and
`risk_data.py`. Keep Agent 0's execution modules unchanged.

- [ ] **Step 7: Run Task 1 tests and self-checks**

Run:

```powershell
python -m unittest tests.test_dv01_pipeline -v
python raw_price_data.py --self-check
rg -n "IBKR_SWAP_COLUMNS|IBKR_TREASURY_COLUMNS|get_ibkr_traded_futures_data|--ibkr" raw_price_data.py signal_data.py risk_data.py config.py
```

Expected: tests and self-check pass; `rg` returns no research-history matches.

- [ ] **Step 8: Commit Task 1**

```powershell
git add data_io.py tests/test_dv01_pipeline.py config.py raw_price_data.py signal_data.py risk_data.py
git commit -m "feat: build canonical CME swap master"
```

### Task 2: Exact-date risk sizing from the master

**Files:**
- Modify: `risk_data.py`
- Modify: `tests/test_dv01_pipeline.py`

**Interfaces:**
- Consumes: `CME_SWAP_DATA_FILE`, `ERIS_SOFR_SWAP_FUTURES`
- Produces: `load_cme_swap_data(path: Path = CME_SWAP_DATA_FILE) -> pd.DataFrame`
- Produces: `merge_cme_dv01(signals: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame`
- Produces: sanitized `build_risk_data(...) -> pd.DataFrame`

- [ ] **Step 1: Write failing risk tests**

Add tests that validate the exact master schema, reject duplicate
`date,ticker`, map YIT to `swap_dv01_per_contract_2y` and YIW to
`swap_dv01_per_contract_5y`, and leave a missing date as `NaN` rather than
forward-filling it. Add a sizing test whose active 2Y signal and DV01 `20.0`
produce the expected rounded swap quantity, and assert that the returned risk
frame has no DV01-named columns.

- [ ] **Step 2: Run the risk tests to verify RED**

```powershell
python -m unittest tests.test_dv01_pipeline -v
```

Expected: failures because master loading/merging is not implemented and the
risk output still exposes DV01 columns.

- [ ] **Step 3: Implement strict master loading and exact-date merge**

Implement:

```python
MASTER_COLUMNS = ["date", "ticker", "price", "dv01"]


def load_cme_swap_data(path: Path = CME_SWAP_DATA_FILE) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}. Run `python raw_price_data.py --eris` first.")
    output = pd.read_csv(path)
    if output.columns.tolist() != MASTER_COLUMNS:
        raise RuntimeError(f"CME swap data must have columns {MASTER_COLUMNS}.")
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.normalize()
    output["ticker"] = output["ticker"].astype("string").str.strip()
    output["price"] = pd.to_numeric(output["price"], errors="coerce")
    output["dv01"] = pd.to_numeric(output["dv01"], errors="coerce")
    if output[MASTER_COLUMNS].isna().any().any() or (output["dv01"] <= 0).any():
        raise RuntimeError("CME swap data contains missing or invalid values.")
    if output.duplicated(["date", "ticker"]).any():
        raise RuntimeError("CME swap data contains duplicate date/ticker rows.")
    return output.sort_values(["date", "ticker"]).reset_index(drop=True)


def merge_cme_dv01(signals: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    output = signals.copy()
    for maturity, root in ERIS_SOFR_SWAP_FUTURES.items():
        maturity_rows = master[master["ticker"].str.startswith(root)]
        if maturity_rows["date"].duplicated().any():
            raise RuntimeError(f"Multiple {maturity} CME contracts selected on one date.")
        column = f"swap_dv01_per_contract_{clean_maturity(maturity)}"
        output = output.merge(
            maturity_rows[["date", "dv01"]].rename(columns={"dv01": column}),
            on="date",
            how="left",
        )
    return output
```

- [ ] **Step 4: Route sizing through the transient merged columns**

Update `add_contract_sizing_for_maturity` to consume
`swap_dv01_per_contract_{m}` without forward-fill, remove the raw wide-column
lookup and actual/fallback switch, and keep its existing contract, notional,
hedge, cap, and risk-flag calculations.

Update `build_risk_data` to call:

```python
signals = load_signal_or_build(...)
master = load_cme_swap_data()
output = build_risk_columns(merge_cme_dv01(signals, master))
output = without_dv01_columns(output)
```

Save with `save_derived_csv` and return the sanitized frame.

- [ ] **Step 5: Run risk tests and self-check**

```powershell
python -m unittest tests.test_dv01_pipeline -v
python risk_data.py --self-check
```

Expected: all pass with exact-date behavior and no DV01 columns in returned
risk data.

- [ ] **Step 6: Commit Task 2**

```powershell
git add risk_data.py tests/test_dv01_pipeline.py
git commit -m "refactor: size risk from CME master"
```

### Task 3: Backtest with transient master risk

**Files:**
- Modify: `backtest.py`
- Modify: `tests/test_dv01_pipeline.py`

**Interfaces:**
- Consumes: `load_cme_swap_data`, `merge_cme_dv01`, rounded swap and Treasury contract columns
- Produces: backtest frames and CSVs with no DV01-named columns

- [ ] **Step 1: Write failing backtest tests**

Add a three-date frame with one rounded 2Y swap contract, one rounded Treasury
hedge contract, 2Y swap returns, Treasury yields, and transient per-contract
DV01. Assert that day-two swap P&L uses the prior contract notional implied by
master DV01, Treasury P&L uses the prior Treasury contract count times the
configured per-contract risk, and the returned frame contains no DV01-named
columns.

- [ ] **Step 2: Run the backtest test to verify RED**

```powershell
python -m unittest tests.test_dv01_pipeline -v
```

Expected: failure because the current backtest requires saved notional and
signed-Treasury-DV01 columns and returns DV01 fields.

- [ ] **Step 3: Attach master data when loading risk rows**

Update `load_signal_frame` so both the saved and refreshed risk paths call
`merge_cme_dv01(risk_frame, load_cme_swap_data())` before
`clean_backtest_frame`.

- [ ] **Step 4: Calculate P&L from contract quantities**

In `add_maturity_pnl`, compute the current swap notional in memory as:

```python
swap_notional = (
    output[swap_contracts_col]
    * output[swap_dv01_col]
    / dv01_per_1mm(SWAP_DV01_YEARS[maturity])
    * 1_000_000
)
treasury_risk = (
    output[treasury_contracts_col]
    * TREASURY_FUTURES_DV01_PER_CONTRACT[maturity]
)
```

Use the shifted swap notional and Treasury risk for daily P&L. Remove prior
target columns and replace active-range detection with nonzero rounded swap
contract quantities. Remove saved-DV01-only exposure summary keys.

- [ ] **Step 5: Sanitize backtest output**

Before saving or returning a backtest frame, call
`without_dv01_columns(backtest)` and save through `save_derived_csv`.

- [ ] **Step 6: Run backtest tests and self-check**

```powershell
python -m unittest tests.test_dv01_pipeline -v
python backtest.py --self-check
```

Expected: all pass; P&L is nonzero for the known contract inputs and the output
contains no DV01 columns.

- [ ] **Step 7: Commit Task 3**

```powershell
git add backtest.py tests/test_dv01_pipeline.py
git commit -m "refactor: backtest from contract quantities"
```

### Task 4: Rebuild, migrate, and verify the full chain

**Files:**
- Modify: `signal_data.py`
- Modify: `raw_price_data.py`
- Modify: `risk_data.py`
- Modify: `backtest.py`
- Modify: existing `data/*.csv` artifacts at runtime

**Interfaces:**
- Consumes: cached settlement files through 2026-07-15
- Produces: rebuilt master, price, signal, risk, and representative backtest CSVs

- [ ] **Step 1: Route every derived writer through the CSV guard**

Replace direct `to_csv` calls for `swap_rates.csv`, `raw_price_data.csv`,
`signal_data.csv`, `risk_data.csv`, and backtest outputs with
`save_derived_csv`. Keep the master direct write as the sole exception.

- [ ] **Step 2: Rebuild cached Eris and raw data**

```powershell
python raw_price_data.py --eris --end 2026-07-15
```

Expected: `cme_swap_data.csv` and price-only `swap_rates.csv` are saved, all
1,474 cached source files are reused, and `raw_price_data.csv` is rebuilt.

- [ ] **Step 3: Rebuild signals and risk**

```powershell
python signal_data.py
python risk_data.py
```

Expected: both files save successfully; risk has active rounded contract rows
where signals and exact-date master DV01 are available.

- [ ] **Step 4: Run a representative backtest**

```powershell
python backtest.py --start 2022-01-01 --end 2024-12-31 --label dv01_master
```

Expected: a nonempty backtest file with P&L rows and no DV01 columns.

- [ ] **Step 5: Verify all code and data**

```powershell
python -m unittest discover -s tests -v
python raw_price_data.py --self-check
python signal_data.py --self-check
python risk_data.py --self-check
python backtest.py --self-check
rg -n "IBKR_SWAP_COLUMNS|IBKR_TREASURY_COLUMNS|get_ibkr_traded_futures_data|--ibkr" raw_price_data.py signal_data.py risk_data.py config.py
```

Run a header scan over `data/*.csv` that fails if any file other than
`cme_swap_data.csv` has a column containing `dv01`. Confirm the master has four
columns, unique `(date,ticker)` keys, YIT and YIW tickers, positive numeric
prices and DV01, ascending dates, and the 2020-09-04 to 2026-07-15 cached range.

- [ ] **Step 6: Review the diff and commit final integration**

```powershell
git diff --check
git status --short
git add data_io.py config.py raw_price_data.py signal_data.py risk_data.py backtest.py tests/test_dv01_pipeline.py
git commit -m "chore: enforce canonical DV01 data flow"
```
