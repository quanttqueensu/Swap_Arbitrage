# P40 Naive Complete-Strategy Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the smallest causal, deterministic naive backtest that invokes the shared strategy boundary, simulates later fills, reconciles exact Decimal accounting, and writes the eight P40 CSV artifacts.

**Architecture:** `backtesting.engine` replays immutable dated snapshots in order and invokes a caller-supplied pure strategy function returning existing Phase 4 records. `backtesting.assumptions` holds visibly named fixed naive costs, while `backtesting.reports` validates and atomically writes result rows. No strategy equation is copied and no existing 99-column result shape is reused.

**Tech Stack:** Python 3.12 standard library (`csv`, `dataclasses`, `datetime`, `decimal`, `pathlib`, `tempfile`) and existing `strategy` records; `unittest` for checks.

## Global Constraints

- Paper-only forever; development submits zero broker orders and performs zero external calls.
- All arithmetic uses finite `Decimal` values and causal event ordering.
- New orders cannot fill on their decision event; they first become eligible on a later replay event satisfying their intent times.
- The run label is `complete_2y_5y`; the manifest also states `synthetic_mechanics_only` because P35 data blockers remain.
- Date-window runs start flat and declare that policy.
- Use no new dependency, copied signal/sizing/risk equation, historical Agent backtest, or wide result table.

---

### Task 1: Frozen assumptions and replay boundary

**Files:**
- Create: `backtesting/__init__.py`
- Create: `backtesting/assumptions.py`
- Create: `backtesting/engine.py`
- Test: `docs/tests/test_naive_backtest.py`

**Interfaces:**
- Consumes: `MarketSnapshot`, `SignalDecision`, `RiskDecision`, and `OrderIntent` from `strategy`.
- Produces: `NAIVE_ASSUMPTIONS`, `NaiveAssumptions`, `ReplayEvent`, `StrategyResult`, and `BacktestResult`.

- [ ] **Step 1: Write the failing boundary test**

```python
def test_naive_assumptions_and_replay_records_fail_closed():
    self.assertEqual(NAIVE_ASSUMPTIONS.commission_usd_per_contract, D("1"))
    with self.assertRaises(ValueError):
        NaiveAssumptions(D("-0.01"), D("1"), D("0"), D("0"), D("0"))
```

- [ ] **Step 2: Run the focused test and observe RED**

Run: `python -m unittest docs.tests.test_naive_backtest -v`

Expected: import failure because `backtesting` does not yet exist.

- [ ] **Step 3: Implement only the frozen records and validation**

```python
@dataclass(frozen=True, slots=True)
class NaiveAssumptions:
    bid_ask_half_spread_points: Decimal
    commission_usd_per_contract: Decimal
    slippage_points: Decimal
    financing_usd_per_contract_day: Decimal
    roll_usd_per_contract: Decimal

@dataclass(frozen=True, slots=True)
class ReplayEvent:
    snapshot: MarketSnapshot
    multipliers_usd_per_point: tuple[tuple[str, Decimal], ...]
    fill_limits: tuple[tuple[str, int], ...] = ()

@dataclass(frozen=True, slots=True)
class StrategyResult:
    decisions: tuple[SignalDecision, ...] = ()
    risk_decisions: tuple[tuple[str, RiskDecision], ...] = ()
    intents: tuple[OrderIntent, ...] = ()
```

Validation rejects non-UTC/unsorted events, duplicate instruments, nonpositive multipliers, negative fill limits, nonfinite costs, and strategy records with mismatched decision times.

- [ ] **Step 4: Run the focused test and observe GREEN**

Run: `python -m unittest docs.tests.test_naive_backtest -v`

Expected: boundary test passes.

### Task 2: Causal fills and exact accounting

**Files:**
- Modify: `backtesting/engine.py`
- Modify: `backtesting/__init__.py`
- Modify: `docs/tests/test_naive_backtest.py`

**Interfaces:**
- Consumes: ordered `ReplayEvent` values and `Callable[[MarketSnapshot], StrategyResult]`.
- Produces: `run_backtest(run_id, events, strategy, assumptions=NAIVE_ASSUMPTIONS, initial_equity_usd=D("1000000"), start_date=None, end_date=None) -> BacktestResult`.

- [ ] **Step 1: Write failing golden tests for event order and fills**

Use literal three-day events. Day 1 is warm-up, day 2 creates an intent, and day 3 fills it. Assert that all three daily rows remain visible, the order cannot fill on day 2, `fill_limits` produces an exact partial fill, and a zero limit produces a rejected fill without changing positions.

- [ ] **Step 2: Run the focused test and observe RED**

Run: `python -m unittest docs.tests.test_naive_backtest.NaiveReplayTests -v`

Expected: failure because `run_backtest` is absent.

- [ ] **Step 3: Implement the minimal replay loop**

```python
for event in window:
    mark_prior_positions(event)
    fill_previously_queued_intents(event)
    result = strategy(snapshot_with_current_positions_and_orders(event))
    queue_new_intents_after_fill_processing(result.intents)
    append_daily_and_position_rows(event)
```

Execution price is current mid plus the signed fixed half-spread and slippage. Transaction cost is the absolute execution-price concession times multiplier plus commission, with the fixed roll charge added only when the matching decision reason contains `roll`. Partial fills keep their unfilled remainder queued; zero capacity records a rejection and removes the order.

- [ ] **Step 4: Write failing accounting/state tests**

Cover entry, exit, direct reversal, roll close/open, financing, prior-position mark P&L, trades, position quantities, equity, and drawdown with hand-derived Decimal literals. Assert for each date:

```python
self.assertEqual(
    row["net_pnl_usd"],
    row["gross_pnl_usd"]
    - row["transaction_cost_usd"]
    - row["financing_cost_usd"],
)
```

- [ ] **Step 5: Implement only enough accounting to pass**

Positions held before an event earn same-contract mark changes to that event. New fills affect positions only after that mark. Financing charges elapsed calendar days on prior absolute contracts. Equity is initial equity plus cumulative net P&L; drawdown is the nonnegative difference from the running equity peak. Gross/net DV01 use current `ContractMetadata` and signed quantities.

- [ ] **Step 6: Write and pass causality/window tests**

Assert an appended future event cannot change any prior decision, order, fill, position, or daily row. Assert an inclusive date window begins flat, includes inactive and risk-blocked dates, and reports missing held-position marks without silently creating zero-price P&L.

- [ ] **Step 7: Run focused tests**

Run: `python -m unittest docs.tests.test_naive_backtest -v`

Expected: all P40 engine tests pass.

### Task 3: Validated P40 CSV artifacts and golden run

**Files:**
- Create: `backtesting/reports.py`
- Modify: `backtesting/__init__.py`
- Modify: `docs/tests/test_naive_backtest.py`
- Create: `docs/verification/P40.md`

**Interfaces:**
- Consumes: `BacktestResult`.
- Produces: `write_results(result, output_root) -> Path` and exactly `manifest.csv`, `daily.csv`, `decisions.csv`, `orders.csv`, `fills.csv`, `trades.csv`, `positions.csv`, `summary.csv`.

- [ ] **Step 1: Write the failing report test**

Write a result to a temporary directory and assert the exact eight filenames, the approved daily header, deterministic ordering, UTF-8 CSV parsing, and byte-identical replacement on a second write.

- [ ] **Step 2: Run the report test and observe RED**

Run: `python -m unittest docs.tests.test_naive_backtest.NaiveReportTests -v`

Expected: import failure for `write_results`.

- [ ] **Step 3: Implement one atomic CSV writer**

```python
def write_results(result: BacktestResult, output_root: Path) -> Path:
    run_dir = output_root / result.run_id
    # Validate exact headers and unique keys, write sibling temporary files,
    # then replace each final CSV only after its rows validate.
    return run_dir
```

The manifest records fixed assumptions, `complete_2y_5y`, `naive`, `synthetic_mechanics_only`, `start_flat`, and strategy/config versions. Summary records exposure, turnover, costs, missing-input count, risk-blocked days, start/end dates, initial/ending equity, and maximum drawdown.

- [ ] **Step 4: Run the focused and repository suites**

Run:

```powershell
python -m unittest docs.tests.test_naive_backtest -v
python -m unittest discover -s docs/tests
python -m unittest discover -s agents/agent_0/tests
python -m compileall -q backtesting strategy docs/tests agents/agent_0
git diff --check
```

Expected: all commands exit `0`.

- [ ] **Step 5: Record the synthetic golden reconciliation**

Write `docs/verification/P40.md` with exact commands/counts, the hand-reconciled short-window rows, TDD RED/GREEN evidence, assumptions, P35 limitation, external/broker contacts `0`, orders submitted `0`, and MG6 requested but not approved.

- [ ] **Step 6: Run one consolidated lower-tier review**

Dispatch at most one Terra reviewer, read-only, covering P40 requirements, accounting identities, causality, test quality, and unnecessary complexity. Resolve every concrete finding locally and rerun affected tests.

- [ ] **Step 7: Inspect and commit the final scoped diff**

Confirm only P40 files and the explicit MG5 ledger line changed, then commit with `feat: add P40 naive causal backtest`.
