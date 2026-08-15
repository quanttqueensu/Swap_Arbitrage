# Rate-Based Swap Spread Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the active Eris-versus-Treasury-futures price-residual signal with an Eris equivalent-par-SOFR-rate minus DGS2/DGS5 Treasury-rate-proxy spread in basis points, without changing execution, risk limits, or futures-price P&L.

**Architecture:** Extend the existing Eris settlement-row extraction to retain the selected contract's rate-conversion inputs and calculate a nullable equivalent par rate before the normal CSV merge. Extend the existing wide pandas signal frame with DGS-based Treasury rate proxies and rate spreads; use their rolling z-scores for positions, maturity ranking, risk strength, and volatility scaling while keeping the old price residual as a diagnostic column. The risk and historical backtest stages continue to consume their existing position and rounded-contract columns, so their hedge construction and price-based accounting do not change.

**Tech Stack:** Python 3.12; pandas 3.0.1; numpy 2.3.5; standard-library unittest; existing CSV pipeline.

## Global Constraints

- Do not add a dependency, database, framework, data vendor, parallel pipeline, migration framework, or new runtime module.
- Use the existing Eris settlement-file route in `data_pipeline/historical_data/historical_data_builder.py`; do not add an Eris curve download.
- The verified official settlement header contains `FinalSettlementPrice`, `Coupon (%)`, `PastFxdFltPmts (B)`, `ErisPAI (C)`, `PV01`, `DV01`, `EffectiveDate`, `MaturityDate`, `LastTradeDate`, `Symbol`, and `ExchangeSymbol (EX005)`. Address these columns by header name only; never by position.
- `FinalSettlementPrice` is the selected settlement-price input. `Coupon (%)` is the contract fixed coupon in percentage points. `PastFxdFltPmts (B)` and `ErisPAI (C)` are USD. `PV01` and `DV01` are USD per bp.
- For each maturity, calculate only when all required inputs are finite and `FinalSettlementPrice > 0` and `PV01 > 0`:

  ```text
  A_usd = (settlement_price - 100 - B_usd + C_usd) * 1000
  equivalent_par_rate_pct = fixed_coupon_pct - (A_usd / PV01_usd_per_bp) / 100
  equivalent_par_rate_bps = equivalent_par_rate_pct * 100
  ```

- Treat missing, nonnumeric, non-finite, and invalid required conversion inputs as unavailable; retain the date if ordinary traded prices are present, emit nullable rate columns, and produce a flat (`0`) position for that maturity.
- DGS2 and DGS5 are daily constant-maturity Treasury-rate proxies in percentage points. Convert them to `treasury_rate_proxy_bps_2y` and `treasury_rate_proxy_bps_5y` by multiplying by 100. They are not CTD-implied yields or IMM-forward-matched rates.
- The active rate-spread columns are `swap_spread_bps_2y` and `swap_spread_bps_5y`; the active z-score columns are `swap_spread_bps_2y_z` and `swap_spread_bps_5y_z`.
- Preserve current `proxy_position_2y`, `proxy_position_5y`, `proxy_signal_2y`, and `proxy_signal_5y` names for sizing, order generation, and historical replay compatibility. Their values must now derive from the rate-spread z-score.
- Preserve the existing price-residual and price-z-score columns when both futures-price columns are present, but do not select them as a position, ranking, strength, or volatility source.
- Do not alter `build_proxy_position()`, entry/exit thresholds, contract selection, DV01 hedge ratios, risk limits, rounded contract sizing, order generation, or any `backtesting/` P&L implementation.
- Run focused tests before the relevant complete suite, and record the exact commands and outcomes in the implementation handoff/commit messages. This plan itself makes no code-behavior claim.

## Current File Map

| File | Existing functions / responsibility | Planned responsibility |
| --- | --- | --- |
| `config.py` | Maps 2Y/5Y swap price, ticker, return, and DV01 column names; defines `MATURITIES`. | Add only the per-maturity output-name maps needed for retained Eris conversion fields and equivalent-par-rate columns. |
| `data_pipeline/historical_data/historical_data_builder.py` | `extract_eris_swap_row()` selects a SOFR contract by exchange root, tenor DV01, and last-trade date; `get_eris_public_swap_data()` builds wide Eris data; `strategy_swap_prices()` produces the raw strategy frame. | Retain header-named fields from the same selected row and add the pure price-to-par-rate conversion without changing selection, roll adjustment, or the CME master. |
| `signal_pipeline.py` | `add_proxy_signal()` calculates price residuals and uses `*_residual_z` for signal and position; `add_best_maturity_columns()` ranks residual z-scores. | Add DGS2/DGS5 rate proxies and rate spreads, then use `swap_spread_bps_*_z` for active signals and rankings while retaining diagnostics. |
| `risk_pipeline.py` | `get_z_col()` and `get_vol_source_col()` select residual columns for current scaling; `build_risk_columns()` sizes and hedges positions. | Point the existing scaling selectors at the active rate-spread z-score and rate-spread level; do not change sizing math or limits. |
| `docs/tests/test_dv01_pipeline.py` | Existing pandas/unit tests for Eris extraction, raw signal-calendar behavior, risk sizing, and exact-date masters. | Add focused conversion, ingestion, signal-source, fail-closed, and price-P&L regression tests in the existing module. |
| `docs/TECHNICAL_DOCUMENTATION.md` | Documents data sources, units, strategy pipeline, and price P&L. | Document source fields, formulas, rate-proxy limitation, active signal, and why no coupon P&L is added. |
| `docs/FILE_MAP.md` | Maps pipeline files to key functions and responsibilities. | Update the historical builder, signal, and risk entries because their key responsibilities materially change. |

**README decision:** Do not modify `README.md` unless implementation review finds an explicit claim that the active strategy signal is a futures-price residual. Its current pipeline-level description is not misleading.

## Task 1: Preserve Eris Rate Inputs and Calculate Equivalent Par Rates

**Files:**

- Modify: `config.py: ERIS_SOFR_SWAP_FUTURES through SWAP_DV01_COLUMNS`
- Modify: `data_pipeline/historical_data/historical_data_builder.py: clean_price_frame(), extract_eris_swap_row(), get_eris_public_swap_data(), strategy_swap_prices()`
- Modify: `docs/tests/test_dv01_pipeline.py: imports and CmeMasterTests`

**Interfaces:**

- Consumes: selected Eris settlement rows with the verified header names above and the current `ERIS_SOFR_SWAP_FUTURES` mapping.
- Produces: `equivalent_par_sofr_swap_rate_bps(settlement_price, fixed_coupon_pct, b_usd, c_usd, pv01_usd_per_bp) -> float | None`; per-maturity `eris_swap_{2y,5y}_equivalent_par_rate_bps`; and retained selected-row fields named `eris_swap_{2y,5y}_{fixed_coupon_pct,b_usd,c_usd,pv01_usd_per_bp,effective_date,maturity_date,last_trade_date}`.
- Preserves: current `eris_swap_*_price`, `*_ticker`, `*_dv01`, return, active-contract, and contract-selection behavior.

- [ ] **Step 1: Write the failing pure-formula and units tests**

  Add the conversion import and these direct tests to `docs/tests/test_dv01_pipeline.py`. Use a zero-`A` case, a one-bp price adjustment case, and the inverse sign case so the formula cannot silently invert:

  ```python
  class ErisEquivalentParRateTests(unittest.TestCase):
      def test_equivalent_par_rate_uses_documented_units_and_sign(self) -> None:
          self.assertEqual(
              equivalent_par_sofr_swap_rate_bps(100.0, 4.50, 0.0, 0.0, 19.0),
              450.0,
          )
          self.assertEqual(
              equivalent_par_sofr_swap_rate_bps(100.019, 4.50, 0.0, 0.0, 19.0),
              449.0,
          )
          self.assertEqual(
              equivalent_par_sofr_swap_rate_bps(99.981, 4.50, 0.0, 0.0, 19.0),
              451.0,
          )

      def test_equivalent_par_rate_rejects_invalid_inputs(self) -> None:
          for values in (
              (None, 4.5, 0.0, 0.0, 19.0),
              (100.0, None, 0.0, 0.0, 19.0),
              (100.0, 4.5, None, 0.0, 19.0),
              (100.0, 4.5, 0.0, None, 19.0),
              (100.0, 4.5, 0.0, 0.0, None),
              (0.0, 4.5, 0.0, 0.0, 19.0),
              (100.0, 4.5, 0.0, 0.0, 0.0),
              (float("nan"), 4.5, 0.0, 0.0, 19.0),
              (100.0, 4.5, float("inf"), 0.0, 19.0),
          ):
              self.assertIsNone(equivalent_par_sofr_swap_rate_bps(*values))
  ```

- [ ] **Step 2: Run the focused formula tests and verify they fail**

  Run:

  ```powershell
  python -m unittest discover -s docs/tests -p test_dv01_pipeline.py -v
  ```

  Expected: import failure for `equivalent_par_sofr_swap_rate_bps` before implementation.

- [ ] **Step 3: Add the smallest explicit field maps and pure converter**

  In `config.py`, add 2Y/5Y output-name maps adjacent to the existing `SWAP_COLUMNS` family. Do not add a generic schema class or a new module. In `historical_data_builder.py`, add one private scalar-normalization helper only if needed to reject bools, missing values, and non-finite values consistently; otherwise use the existing pandas numeric conversion locally. Add `equivalent_par_sofr_swap_rate_bps()` beside `extract_eris_swap_row()` with this body logic:

  ```python
  a_usd = (settlement_price - 100.0 - b_usd + c_usd) * 1000.0
  equivalent_par_rate_pct = fixed_coupon_pct - (a_usd / pv01_usd_per_bp) / 100.0
  return equivalent_par_rate_pct * 100.0
  ```

  Return `None` before calculation unless all five inputs are real finite scalars, settlement price is strictly positive, and PV01 is strictly positive. Do not require B or C to be positive because payment and PAI adjustments can be signed.

  In `extract_eris_swap_row()`, after the current selected row is chosen, read these exact headers using `selected.get(...)`: `FinalSettlementPrice`, `Coupon (%)`, `PastFxdFltPmts (B)`, `ErisPAI (C)`, `PV01`, `DV01`, `EffectiveDate`, `MaturityDate`, and `LastTradeDate`. Write the raw numeric and date values to the configured per-maturity columns when present. Call the pure converter and write its output only when it returns a finite number. Continue to retain `FinalSettlementPrice` in the existing price column and preserve the existing `DV01` handling.

  Update the Eris-specific final formatting only as needed so the three retained metadata dates survive `clean_price_frame()` rather than being coerced to numeric; normalize them with `pd.to_datetime(..., errors="coerce").dt.normalize()`. Keep rate-conversion fields numeric. Include those retained fields and the calculated rate in the ordered Eris output. Keep `strategy_swap_prices()` narrow: it must retain equivalent-par-rate columns needed by the raw signal data but continue to omit tickers, DV01, source accounting inputs, and metadata dates from the strategy price file.

- [ ] **Step 4: Extend the selected-row test with real header names**

  Expand the existing `test_active_contract_extraction_preserves_full_ticker` fixture with the official header names and one date value:

  ```python
  "Coupon (%)": [4.50, 4.60],
  "PastFxdFltPmts (B)": [0.0, 0.0],
  "ErisPAI (C)": [0.0, 0.0],
  "PV01": [19.0, 20.0],
  "EffectiveDate": ["12/20/2023", "03/20/2024"],
  "MaturityDate": ["12/20/2025", "03/20/2026"],
  ```

  Assert that the selected 2Y row still has `YITH24`, `99.1`, and `19.0`, that all seven retained fields are present with the selected-row values, and that `eris_swap_2y_equivalent_par_rate_bps` equals the pure-function result. Add a second extraction case whose `PV01` is zero or whose B field is absent; assert no equivalent-par-rate key is emitted while the ordinary selected price and ticker remain available.

- [ ] **Step 5: Run focused ingestion/conversion tests and verify they pass**

  Run:

  ```powershell
  python -m unittest discover -s docs/tests -p test_dv01_pipeline.py -v
  ```

  Expected: the new `ErisEquivalentParRateTests` and expanded `CmeMasterTests` pass, along with the pre-existing tests in that module.

- [ ] **Step 6: Commit the independently tested ingestion change**

  ```powershell
  git add config.py data_pipeline/historical_data/historical_data_builder.py docs/tests/test_dv01_pipeline.py
  git commit -m "feat: derive Eris equivalent par SOFR rates"
  ```

### Task 2: Make DGS Rate Spreads the Active Signal Without Changing Price P&L

**Files:**

- Modify: `signal_pipeline.py: add_proxy_signal(), add_best_maturity_columns(), build_signal_columns()`
- Modify: `risk_pipeline.py: get_z_col(), get_vol_source_col()`
- Modify: `docs/tests/test_dv01_pipeline.py: SignalCalendarTests and RiskMasterTests`
- Modify: `docs/tests/test_historical_backtest.py: HistoricalRunTests`

**Interfaces:**

- Consumes: `eris_swap_2y_equivalent_par_rate_bps`, `eris_swap_5y_equivalent_par_rate_bps`, `dgs2`, `dgs5`, and existing futures-price columns.
- Produces: `treasury_rate_proxy_bps_2y`, `treasury_rate_proxy_bps_5y`, `swap_spread_bps_2y`, `swap_spread_bps_5y`, their `_z` columns, and the existing `proxy_signal_*`/`proxy_position_*` outputs derived exclusively from those z-scores.
- Preserves: price residual diagnostic columns, position state machine, risk-limit/contract-sizing formulas, rounded contract column names, and `backtesting.historical` price accounting.

- [ ] **Step 1: Write a failing rate-source/position test**

  In `docs/tests/test_dv01_pipeline.py`, construct a four-row raw DataFrame containing valid 2Y/5Y traded prices, valid equivalent-par-rate columns, `dgs2`/`dgs5`, and deliberately changing Treasury-futures prices. Patch `signal_pipeline.ROLLING_WINDOW` to `3` and `signal_pipeline.MIN_PERIODS` to `2` while calling `build_signal_columns()`. Assert exact rate conversion and source routing:

  ```python
  self.assertEqual(output["treasury_rate_proxy_bps_2y"].tolist(), [400.0, 400.0, 400.0, 400.0])
  self.assertEqual(output["swap_spread_bps_2y"].tolist(), [10.0, 30.0, 50.0, 70.0])
  self.assertEqual(
      output["proxy_position_2y"].tolist(),
      build_proxy_position(output["swap_spread_bps_2y_z"]).tolist(),
  )
  self.assertNotEqual(
      output["proxy_position_2y"].tolist(),
      build_proxy_position(output["eris_swap_2y_price_residual_z"]).tolist(),
  )
  ```

  Choose the price series so the final residual z-score would produce the opposite state from the rate-spread z-score. Also assert the legacy residual columns still exist for diagnostics.

- [ ] **Step 2: Run the focused signal test and verify it fails**

  Run:

  ```powershell
  python -m unittest discover -s docs/tests -p test_dv01_pipeline.py -v
  ```

  Expected: missing `treasury_rate_proxy_bps_2y` and `swap_spread_bps_2y` columns, or an assertion showing the old residual remains the signal source.

- [ ] **Step 3: Add proxy, spread, and active z-score columns in the existing signal function**

  Keep `add_proxy_signal()` as the single per-maturity entry point. Resolve `dgs2` for `2Y` and `dgs5` for `5Y` with a local two-item mapping. Populate `treasury_rate_proxy_bps_{maturity}` as numeric DGS percentage points times `100.0`; populate `swap_spread_bps_{maturity}` as equivalent par rate minus that proxy; set both to `NaN` when either component is unavailable or non-finite.

  Keep the current `price_z`, `residual_vs_treasury`, and `residual_z` calculation conditional on the existing futures-price columns. Replace only the active `source` assignment:

  ```python
  rate_spread_z_col = f"swap_spread_bps_{maturity_key}_z"
  output[rate_spread_z_col] = rolling_zscore(output[rate_spread_col])
  source = output[rate_spread_z_col]
  ```

  Use `source` for the existing signal thresholds and `build_proxy_position()`; do not fall back to price residual or price z-score when conversion/proxy data are absent. Missing rate inputs therefore yield a flat state through the existing null-z-score branch.

  Update `add_best_maturity_columns()` to rank `swap_spread_bps_*_z`. Update only `risk_pipeline.get_z_col()` to return the corresponding rate-spread z-score and `get_vol_source_col()` to return the corresponding rate-spread level; leave the scale formulas and all downstream column names untouched.

- [ ] **Step 4: Add fail-closed and risk-source regression tests**

  Add a raw-frame test where one 2Y equivalent-par-rate value is `NaN` but traded prices and DGS2 exist. Assert that its 2Y rate spread and z-score are null and `proxy_position_2y == 0`; assert the 5Y row can remain independently valid. Add a `build_risk_data(save=False)` test with rate-spread columns and intentionally contradictory old residual columns:

  ```python
  "proxy_position_2y": [1],
  "swap_spread_bps_2y": [25.0],
  "swap_spread_bps_2y_z": [2.0],
  "eris_swap_2y_price_residual_vs_treasury": [999.0],
  "eris_swap_2y_price_residual_z": [0.0],
  ```

  Assert the existing nonzero rounded swap and Treasury hedge quantities are retained. This proves sizing gets strength and volatility from the active rate-spread source rather than the residual.

  In `docs/tests/test_historical_backtest.py`, add a regression that runs the existing `holding_frame()` with zero costs, records the established `gross_pnl_usd`, `net_pnl_usd`, and equity assertions, then adds representative rate-spread diagnostic columns and reruns it. Assert the daily and summary price-P&L outputs are identical. Do not modify `backtesting/historical.py` or `backtesting/engine.py`.

- [ ] **Step 5: Run focused signal, sizing, and P&L regressions**

  Run:

  ```powershell
  python -m unittest discover -s docs/tests -p test_dv01_pipeline.py -v
  python -m unittest discover -s docs/tests -p test_historical_backtest.py -v
  ```

  Expected: all newly added rate-source, fail-closed, risk-sizing, and unchanged-P&L tests pass.

- [ ] **Step 6: Commit the independently tested signal routing change**

  ```powershell
  git add signal_pipeline.py risk_pipeline.py docs/tests/test_dv01_pipeline.py docs/tests/test_historical_backtest.py
  git commit -m "feat: drive swap signals from rate spreads"
  ```

### Task 3: Document the Rate-Based Signal and Verify the Whole Relevant Suite

**Files:**

- Modify: `docs/TECHNICAL_DOCUMENTATION.md: Data sources, historical-data flow, units/signs, and P&L reference`
- Modify: `docs/FILE_MAP.md: historical data, root pipeline, and test responsibility tables`
- Verify only: `README.md`

**Interfaces:**

- Consumes: the implemented field names, formula, signal columns, and unchanged futures-price P&L behavior from Tasks 1 and 2.
- Produces: an operator-facing explanation that precisely labels DGS2/DGS5 as proxies and records the scope boundary.

- [ ] **Step 1: Add a documentation acceptance test before editing prose**

  In the existing documentation test location, add a focused assertion to the closest maintained-documentation test module that reads `docs/TECHNICAL_DOCUMENTATION.md` and requires these phrases or code identifiers:

  ```python
  self.assertIn("equivalent_par_rate_bps", text)
  self.assertIn("swap_spread_bps", text)
  self.assertIn("DGS2/DGS5", text)
  self.assertIn("not CTD", text)
  self.assertIn("coupon P&L is not added separately", text)
  ```

  If no current documentation test is scoped to this strategy document, place this small test in `docs/tests/test_dv01_pipeline.py` rather than adding a test utility or new framework.

- [ ] **Step 2: Run the focused documentation test and verify it fails**

  Run:

  ```powershell
  python -m unittest discover -s docs/tests -p test_dv01_pipeline.py -v
  ```

  Expected: the required rate-based terms are absent from current technical documentation.

- [ ] **Step 3: Update technical documentation and the file map**

  In `docs/TECHNICAL_DOCUMENTATION.md`, add a concise “Rate-based swap-spread signal” subsection near the existing data-source/units material. State the exact source header names and timing: one selected daily Eris settlement row per maturity, `FinalSettlementPrice`, `Coupon (%)`, B, C, PV01, effective/maturity/last-trade dates, plus same-date DGS2/DGS5. Include the complete formula with units and:

  ```text
  swap_spread_bps_2y = eris_swap_2y_equivalent_par_rate_bps - treasury_rate_proxy_bps_2y
  swap_spread_bps_5y = eris_swap_5y_equivalent_par_rate_bps - treasury_rate_proxy_bps_5y
  ```

  State that the rolling z-score of each `swap_spread_bps_*` drives entry/exit and that price residuals remain diagnostics. State that DGS2/DGS5 are Treasury constant-maturity rate proxies, not CTD-implied yields and not forward-start/IMM-aligned Treasury rates. In the P&L section, state that historical P&L remains quantity × price multiplier × futures price change; the Eris futures settlement price already reflects swap NPV, coupon accrual/payment effects, and PAI, so no separate fixed/floating coupon P&L is added.

  Update the relevant `docs/FILE_MAP.md` rows to name the new conversion and rate-spread responsibilities and the expanded pipeline test coverage. Review `README.md`; make no change unless it explicitly claims price-residual-driven entries.

- [ ] **Step 4: Add intentionally deferred scope to the technical documentation**

  Add this exact short list under the rate-signal subsection:

  - CTD-implied Treasury yields and delivery-basket/conversion-factor modeling.
  - IMM-forward-start curve matching between each Eris swap and Treasury comparator.
  - New market-data vendors or a separate Eris curve download.
  - Database, pipeline, framework, or infrastructure redesign.

- [ ] **Step 5: Run focused and complete relevant verification**

  Run the focused checks first:

  ```powershell
  python -m unittest discover -s docs/tests -p test_dv01_pipeline.py -v
  python -m unittest discover -s docs/tests -p test_historical_backtest.py -v
  ```

  Then run the repository’s relevant documented suite:

  ```powershell
  python -m unittest discover -s docs/tests -v
  ```

  Record each command, exit code, and pass/fail count in the implementation report. If any unrelated existing test fails, record it separately without weakening, skipping, or deleting a test.

- [ ] **Step 6: Commit the documentation and verification change**

  ```powershell
  git add docs/TECHNICAL_DOCUMENTATION.md docs/FILE_MAP.md docs/tests/test_dv01_pipeline.py
  git commit -m "docs: describe rate-based swap spread signals"
  ```

## Intentionally Deferred

- CTD-implied Treasury yields, delivery-basket selection, and conversion-factor engines.
- Treasury forward-rate construction and matching to each Eris IMM effective date.
- New data vendors and a standalone Eris curve/market-data download path.
- Separate fixed/floating coupon P&L, because Eris futures settlement-price P&L already captures the contract economics.
- Any database, migration, framework, or infrastructure redesign.

## Plan Self-Review

- **Spec coverage:** Task 1 covers header-based Eris ingestion, retained fields, explicit units, and conversion fail-closed behavior. Task 2 covers DGS2/DGS5 proxies, rate-spread signal routing, diagnostics-only residuals, unchanged risk sizing mechanics, zero positions on missing conversion data, and unchanged futures-price P&L. Task 3 covers technical documentation, file-map update, README decision, deferrals, and focused/full verification.
- **Placeholder scan:** No task relies on positional columns, an unnamed source field, a generic “handle errors” instruction, or unspecified test behavior.
- **Type/name consistency:** The plan uses `eris_swap_{2y,5y}_equivalent_par_rate_bps`, `treasury_rate_proxy_bps_{2y,5y}`, `swap_spread_bps_{2y,5y}`, and `swap_spread_bps_{2y,5y}_z` consistently. Existing public position/signal names remain unchanged for downstream compatibility.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-15-rate-based-swap-spread-signals.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task and review between tasks for fast iteration.
2. **Inline Execution** — execute the tasks in this session using `superpowers:executing-plans`, with checkpoints for review.

Which approach?
