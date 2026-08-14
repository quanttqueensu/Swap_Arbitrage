# P40B Approved Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply only the user's approved TF-002, TF-003, and TF-004 changes while leaving every ignored finding unchanged.

**Architecture:** Keep the existing replay engine and approved `data_pipeline.contracts` schema catalog. Add precise missing-input locations to the manifest, relabel generic synthetic runs, and adapt engine results into the approved backtest CSV rows. Extend the existing engine only as far as necessary to produce truthful trade-lifecycle and position-accounting fields.

**Tech Stack:** Python 3.12 standard library, `unittest`, existing `Decimal`-based strategy and backtest records.

## Global Constraints

- Preserve unrelated working-tree changes.
- Do not connect to external services or IBKR.
- Do not change TF-001 or TF-005 through TF-011.
- Do not create `docs/TECHNICAL_DOCUMENTATION.md`.
- Keep current partial accounting when held data is missing; expose exact date, instrument, and missing field in output.
- Preserve approved `data_pipeline.contracts.SCHEMAS` meanings and version.
- Reserve `complete_2y_5y`; generic P40 mechanics output uses a synthetic-only scope.
- Use no new dependency or speculative abstraction.

---

### Task 1: Record MG6A dispositions

**Files:**
- Modify: `docs/audits/technical-foundation-audit.md`
- Modify: `docs/master-plan/VERIFICATION_GATES.md`

**Interfaces:**
- Consumes: user dispositions from 2026-08-09.
- Produces: an auditable authorization record for the limited P40B scope.

- [ ] **Step 1: Record each disposition exactly**

Record TF-001 and TF-005 through TF-011 as ignored/no change; TF-002 as option C with exact output diagnostics; TF-003 as option A; TF-004 as the recommended relabeling.

- [ ] **Step 2: Mark only the MG6A authorization checkpoint**

Set the gate row to authorization approved with P40B completion pending. Do not claim MG6A completion.

- [ ] **Step 3: Inspect the diff**

Run: `git diff -- docs/master-plan/VERIFICATION_GATES.md` and read the ignored audit file directly.

Expected: only the authorized status/disposition text changes.

### Task 2: Make missing-input locations and synthetic scope explicit

**Files:**
- Modify: `docs/tests/test_naive_backtest.py`
- Modify: `backtesting/engine.py`

**Interfaces:**
- Consumes: existing `ReplayEvent` data and partial-accounting behavior.
- Produces: manifest key `missing_input_locations` containing deterministic `date:instrument_id:field` entries; `maturity_scope=synthetic_fixture`.

- [ ] **Step 1: Write failing behavior tests**

Extend `test_risk_blocked_dates_and_missing_marks_are_counted` to assert the literal location `2026-01-04:YITH27:current_mark` and partial equity behavior. Update the manifest test to require `synthetic_fixture` and reject `complete_2y_5y`.

- [ ] **Step 2: Run focused tests and observe RED**

Run: `python -m unittest docs.tests.test_naive_backtest.NaiveReplayTests.test_risk_blocked_dates_and_missing_marks_are_counted docs.tests.test_naive_backtest.NaiveReportTests.test_writes_exact_validated_csv_set_deterministically -v`

Expected: failures because the location key is absent and the old maturity label remains.

- [ ] **Step 3: Implement the minimum engine change**

Keep the current P&L branch. Replace the instrument-only diagnostic set with deterministic `(date, instrument_id, field)` evidence for current mark, previous mark, multiplier, execution mark/multiplier, and contract metadata. Add one manifest value by joining sorted locations with `;`. Change only the generic maturity label to `synthetic_fixture`.

- [ ] **Step 4: Run focused tests and observe GREEN**

Run the Task 2 focused command again.

Expected: both tests pass without changing the existing partial-accounting result.

### Task 3: Emit the approved backtest schemas

**Files:**
- Modify: `docs/tests/test_naive_backtest.py`
- Modify: `backtesting/engine.py`
- Modify: `backtesting/reports.py`

**Interfaces:**
- Consumes: `BacktestResult`, `SCHEMAS`, `validate_csv`, fills, decisions, positions, and fixed assumptions.
- Produces: `daily.csv`, `decisions.csv`, `orders.csv`, `fills.csv`, `trades.csv`, `positions.csv`, and `summary.csv` that all pass their existing `backtest_*` schema contracts.

- [ ] **Step 1: Write a failing generated-report integration test**

Run the existing five-day entry/exit strategy, call `write_results`, and assert:

```python
for filename, schema_id in {
    "daily.csv": "backtest_daily",
    "decisions.csv": "backtest_decisions",
    "orders.csv": "backtest_orders",
    "fills.csv": "backtest_fills",
    "trades.csv": "backtest_trades",
    "positions.csv": "backtest_positions",
    "summary.csv": "backtest_summary",
}.items():
    self.assertGreaterEqual(validate_csv(SCHEMAS[schema_id], run_dir / filename), 0)
```

Assert one closed 2Y trade with literal open/close times and `net_pnl_usd = gross_pnl_usd - cost_usd`. Assert signed SELL quantities and literal summary fields.

- [ ] **Step 2: Run the integration test and observe RED**

Run: `python -m unittest docs.tests.test_naive_backtest.NaiveReportTests.test_generated_reports_match_approved_schema_catalog -v`

Expected: header mismatch on the first non-daily artifact.

- [ ] **Step 3: Implement canonical result records**

Change `TradeRecord` to the approved lifecycle fields and `PositionRecord` to the approved daily position fields. Reuse current fill execution data to maintain per-instrument average cost and cumulative realized gross P&L. Record daily market value and unrealized gross P&L with `Decimal` arithmetic.

- [ ] **Step 4: Implement minimal trade lifecycle accounting**

Maintain at most one active trade per maturity. Accumulate held-leg gross P&L, financing, and execution cost into that trade. Entry opens it, roll/resizing retains it, exit closes it, and reversal closes the old direction before opening the new direction. Split reversal execution cost between closing and opening quantities using actual pre-fill position quantities. Leave a partially filled transition open until its remaining orders finish.

- [ ] **Step 5: Implement report adapters and schema validation**

In `backtesting/reports.py`, map shared decisions and intents to the existing catalog columns; derive stable `order-N` and `fill-N` identifiers; use signed order/fill quantities; hash `configuration_version` into `config_hash`; emit the approved one-row summary. Sort by each catalog ordering and validate temporary CSVs with `validate_csv` before replacement.

- [ ] **Step 6: Run the integration test and observe GREEN**

Run the Task 3 focused command again.

Expected: every generated artifact validates and the closed trade reconciles.

- [ ] **Step 7: Run all P40 tests**

Run: `python -m unittest docs.tests.test_naive_backtest -v`

Expected: all P40 tests pass.

### Task 4: Final evidence and audit update

**Files:**
- Modify: `docs/audits/technical-foundation-audit.md`

**Interfaces:**
- Consumes: focused/full verification results.
- Produces: final status for TF-002, TF-003, and TF-004 without claiming ignored items were fixed.

- [ ] **Step 1: Run full offline verification**

Run the documentation suite, Agent 0 suite, compileall, three legacy self-checks, `git diff --check`, schema/reference scans, and an account-identifier scan that does not print matched secret text.

- [ ] **Step 2: Update implemented finding evidence**

Mark only TF-002, TF-003, and TF-004 implemented with exact commands/results. Keep ignored findings explicit and keep MG6A incomplete because TF-010 was declined and ignored high findings remain.

- [ ] **Step 3: Inspect final scope**

Run: `git status --short` and `git diff --stat`.

Expected: no unrelated change was overwritten; the audit remains ignored because TF-007 was declined.
