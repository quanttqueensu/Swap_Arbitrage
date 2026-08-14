# Backtesting Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `backtesting/` the repository's only runnable historical backtest path, emit its canonical reports, and remove `backtest_engine.py`.

**Architecture:** A focused `backtesting.historical` adapter converts the existing signal/risk DataFrame and market-master columns into `ReplayEvent` values and a deterministic strategy callback. The existing replay engine remains the only execution/accounting ledger, `write_results` remains the only report writer, and `backtesting.__main__` becomes the only CLI.

**Tech Stack:** Python 3.12, existing pandas/numpy dependencies for legacy signal/risk input conversion, standard-library `argparse`/`datetime`/`decimal`/`pathlib`, existing `unittest`, existing `strategy` records, existing canonical CSV validators.

## Global Constraints

- Preserve all pre-existing staged and unstaged user changes; never reset, restore, or overwrite them.
- `backtesting/engine.py`, `backtesting/reports.py`, `docs/tests/test_naive_backtest.py`, `README.md`, and `docs/TECHNICAL_DOCUMENTATION.md` already contain user changes; inspect their diffs before every edit and stage only migration-owned hunks.
- Add no dependency, alternate engine, compatibility wrapper, second position ledger, copied strategy/risk equation, or legacy wide-CSV output.
- Keep `signal_pipeline.py` and `risk_pipeline.py` as the current upstream historical preparation boundary.
- Preserve causal timing: a decision created on one event may fill only on a later eligible event.
- Treat new replay accounting as authoritative; do not force date-by-date parity with the legacy timing shortcut.
- Default CLI execution is offline and uses local files; only `--refresh-signals` may trigger the existing explicit refresh path.
- Fail closed on invalid active ticker, price, DV01, multiplier, duplicate date, duplicate instrument, empty window, or reversed window.
- Use exact `Decimal` construction from strings for money, prices, DV01, multipliers, and CLI numeric arguments.
- Keep the canonical output set exactly `manifest.csv`, `daily.csv`, `decisions.csv`, `orders.csv`, `fills.csv`, `trades.csv`, `positions.csv`, and `summary.csv`.
- Historical roll closure uses an explicit `last_pre_roll_mark_zero_return` research proxy for the retiring ticker and records that policy in the manifest; it is not silent forward-fill or production roll evidence.
- Remove `backtest_engine.py` only after focused adapter, replay, CLI, and migrated behavior tests pass.
- Update `README.md`, `docs/TECHNICAL_DOCUMENTATION.md`, `docs/FILE_MAP.md`, and `docs/FUNCTION_INVENTORY.md` after the final public API exists.

## File Structure

- Create `backtesting/historical.py`: load/clean the existing historical frame, convert events, derive target-change decisions/intents, run the replay, and write canonical results.
- Create `backtesting/__main__.py`: parse the one supported CLI and provide its offline self-check.
- Modify `backtesting/__init__.py`: export only `run_historical_backtest` in addition to the existing replay/report API.
- Create `docs/tests/test_historical_backtest.py`: adapter, causal accounting, roll, validation, orchestration, and CLI tests.
- Modify `docs/tests/test_dv01_pipeline.py`: remove legacy backtest imports and `BacktestMasterTests`; retain signal/risk/master tests.
- Modify `docs/tests/test_import_smoke.py`: import `backtesting` and `backtesting.__main__`, not the deleted root module.
- Delete `backtest_engine.py`: remove the duplicate DataFrame execution/accounting implementation.
- Modify `README.md`, `docs/TECHNICAL_DOCUMENTATION.md`, `docs/FILE_MAP.md`, and `docs/FUNCTION_INVENTORY.md`: document one backtest command, flow, file map, and public function inventory.

Before Task 1, set the verified Python 3.12 runtime once for every PowerShell
command in this plan:

```powershell
$python = 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $python --version
```

Expected: `Python 3.12.13`.

---

### Task 1: Convert the historical frame into causal replay events

**Files:**
- Create: `backtesting/historical.py`
- Create: `docs/tests/test_historical_backtest.py`

**Interfaces:**
- Consumes: `risk_pipeline.build_risk_data`, `risk_pipeline.load_cme_swap_data`, `risk_pipeline.load_treasury_futures_data`, `risk_pipeline.merge_cme_dv01`, `risk_pipeline.merge_treasury_futures_data`, `config.MATURITIES`, `config.RISK_DATA_FILE`, `config.ERIS_DOLLARS_PER_POINT`, `config.TREASURY_FUTURES_DOLLARS_PER_POINT`.
- Produces: `_load_historical_frame(refresh_signals: bool) -> pd.DataFrame` and `_events_from_frame(frame: pd.DataFrame) -> tuple[ReplayEvent, ...]` for Task 2.

- [ ] **Step 1: Write failing frame and event conversion tests**

Create `docs/tests/test_historical_backtest.py` with a compact reusable fixture and tests that demand ordered typed events, exact multipliers, and fail-closed validation:

```python
from datetime import timezone
from decimal import Decimal
import unittest

import pandas as pd

from backtesting.historical import _events_from_frame


D = Decimal


def historical_frame():
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]),
        "risk_allowed": [1, 1, 1, 1],
        "risk_block_reason": ["", "", "", ""],
        "proxy_position_2y": [0, 1, 1, 0],
        "swap_futures_contracts_rounded_2y": [0, 2, 2, 0],
        "treasury_futures_contracts_rounded_2y": [0, -1, -1, 0],
        "swap_ticker_2y": ["YITH24"] * 4,
        "treasury_ticker_2y": ["ZTH24"] * 4,
        "swap_price_2y": [100.0, 100.0, 100.1, 100.1],
        "treasury_price_2y": [102.0, 102.0, 101.99, 101.99],
        "swap_dv01_per_contract_2y": [19.0] * 4,
        "treasury_dv01_per_contract_2y": [38.0] * 4,
    })


class HistoricalEventTests(unittest.TestCase):
    def test_rows_become_ordered_typed_events(self):
        events = _events_from_frame(historical_frame())
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0].snapshot.decision_time_utc.tzinfo, timezone.utc)
        self.assertEqual(
            dict(events[0].multipliers_usd_per_point),
            {"YITH24": D("1000.0"), "ZTH24": D("2000.0")},
        )
        self.assertEqual(
            [item.instrument_id for item in events[0].snapshot.contracts],
            ["YITH24", "ZTH24"],
        )

    def test_duplicate_dates_and_invalid_active_fields_fail_closed(self):
        duplicate = pd.concat([historical_frame(), historical_frame().iloc[[0]]])
        with self.assertRaisesRegex(RuntimeError, "duplicate date"):
            _events_from_frame(duplicate)

        invalid = historical_frame()
        invalid.loc[1, "swap_price_2y"] = float("nan")
        with self.assertRaisesRegex(RuntimeError, "positive price/DV01 and ticker"):
            _events_from_frame(invalid)
```

- [ ] **Step 2: Run the focused test and observe RED**

Run:

```powershell
& $python -m unittest docs.tests.test_historical_backtest.HistoricalEventTests -v
```

Expected: import error because `backtesting.historical` does not exist.

- [ ] **Step 3: Implement the minimum loader and event converter**

In `backtesting/historical.py`, reuse the root loader behavior without importing `backtest_engine.py`:

```python
from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal

import pandas as pd

from backtesting.engine import ReplayEvent
from config import (
    ERIS_DOLLARS_PER_POINT,
    MATURITIES,
    RISK_DATA_FILE,
    TREASURY_FUTURES_DOLLARS_PER_POINT,
)
from risk_pipeline import (
    build_risk_data,
    load_cme_swap_data,
    load_treasury_futures_data,
    merge_cme_dv01,
    merge_treasury_futures_data,
)
from signal_pipeline import clean_maturity
from strategy import ContractMetadata, InstrumentObservation, MarketSnapshot


UTC = timezone.utc


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _load_historical_frame(refresh_signals: bool = False) -> pd.DataFrame:
    if refresh_signals:
        risk = build_risk_data(refresh_signals=True, save=False)
    else:
        if not RISK_DATA_FILE.exists():
            raise FileNotFoundError(
                f"Missing {RISK_DATA_FILE}. Run `python risk_pipeline.py` first."
            )
        risk = pd.read_csv(RISK_DATA_FILE)
    merged = merge_cme_dv01(risk, load_cme_swap_data(), include_tickers=True)
    merged = merge_treasury_futures_data(
        merged, load_treasury_futures_data(), include_market_data=True
    )
    output = merged.copy()
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.normalize()
    if output["date"].isna().any() or output["date"].duplicated().any():
        raise RuntimeError("historical data contains an invalid or duplicate date")
    return output.sort_values("date").reset_index(drop=True)
```

Implement `_events_from_frame` with these exact rules:

```python
def _events_from_frame(frame: pd.DataFrame) -> tuple[ReplayEvent, ...]:
    if "date" not in frame:
        raise RuntimeError("historical data must contain date")
    rows = frame.copy()
    rows["date"] = pd.to_datetime(rows["date"], errors="coerce").dt.normalize()
    if rows.empty or rows["date"].isna().any() or rows["date"].duplicated().any():
        raise RuntimeError("historical data contains an invalid or duplicate date")
    rows = rows.sort_values("date").reset_index(drop=True)
    events = []
    for _, row in rows.iterrows():
        timestamp = datetime.combine(row["date"].date(), time(21), tzinfo=UTC)
        instruments = []
        contracts = []
        multipliers = []
        for maturity in MATURITIES:
            m = clean_maturity(maturity)
            for leg, multiplier in (
                ("swap", ERIS_DOLLARS_PER_POINT),
                ("treasury", TREASURY_FUTURES_DOLLARS_PER_POINT[maturity]),
            ):
                quantity = int(row.get(f"{leg}_futures_contracts_rounded_{m}", 0))
                ticker = str(row.get(f"{leg}_ticker_{m}", "")).strip()
                price = pd.to_numeric(row.get(f"{leg}_price_{m}"), errors="coerce")
                dv01 = pd.to_numeric(
                    row.get(f"{leg}_dv01_per_contract_{m}"), errors="coerce"
                )
                if quantity and (not ticker or not price > 0 or not dv01 > 0):
                    raise RuntimeError(
                        f"Nonzero {maturity} {leg} contracts require positive price/DV01 and ticker"
                    )
                if not ticker or not price > 0 or not dv01 > 0:
                    continue
                instruments.append(
                    InstrumentObservation(ticker, _decimal(price), "historical_master", timestamp, timestamp)
                )
                contracts.append(ContractMetadata(ticker, maturity, _decimal(dv01), -1))
                multipliers.append((ticker, _decimal(multiplier)))
        events.append(ReplayEvent(
            MarketSnapshot(timestamp, (), tuple(instruments), tuple(contracts)),
            tuple(multipliers),
        ))
    return tuple(events)
```

Deduplicate instrument IDs within each event and raise `RuntimeError` on conflicting price, DV01, maturity, or multiplier values. Do not forward-fill ordinary missing fields.

- [ ] **Step 4: Run the focused tests and observe GREEN**

Run:

```powershell
& $python -m unittest docs.tests.test_historical_backtest.HistoricalEventTests -v
```

Expected: all `HistoricalEventTests` pass.

- [ ] **Step 5: Commit only the new adapter conversion slice**

```powershell
git add backtesting/historical.py docs/tests/test_historical_backtest.py
git diff --cached --check
git commit -m "feat: adapt historical rows to replay events"
```

---

### Task 2: Derive causal target orders and run canonical historical results

**Files:**
- Modify: `backtesting/historical.py`
- Modify: `docs/tests/test_historical_backtest.py`
- Modify: `backtesting/__init__.py`

**Interfaces:**
- Consumes: `_load_historical_frame`, `_events_from_frame`, `backtesting.run_backtest`, `backtesting.write_results`, `NaiveAssumptions`, and the existing Phase 4 records.
- Produces: `run_historical_backtest(run_id: str, output_root: Path, start: str = "auto", end: str = "auto", refresh_signals: bool = False, assumptions: NaiveAssumptions = NAIVE_ASSUMPTIONS, initial_equity_usd: Decimal = Decimal("1000000")) -> tuple[BacktestResult, Path]`.

- [ ] **Step 1: Write failing causal target, risk, roll, and report tests**

Extend `docs/tests/test_historical_backtest.py`:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from backtesting import NAIVE_ASSUMPTIONS
from backtesting.historical import run_historical_backtest


class HistoricalRunTests(unittest.TestCase):
    def test_targets_fill_later_and_pnl_uses_only_held_positions(self):
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=historical_frame()):
                result, run_dir = run_historical_backtest(
                    "historical-golden", Path(directory), assumptions=NAIVE_ASSUMPTIONS
                )
        self.assertEqual([fill.fill_time_utc.date().isoformat() for fill in result.fills[:2]], [
            "2024-01-04", "2024-01-04",
        ])
        self.assertEqual(result.daily[1].gross_pnl_usd, D("0"))
        self.assertEqual(result.daily[2].gross_pnl_usd, D("0"))
        self.assertEqual(result.daily[3].gross_pnl_usd, D("0"))
        self.assertEqual(
            sorted(path.name for path in run_dir.iterdir()),
            ["daily.csv", "decisions.csv", "fills.csv", "manifest.csv", "orders.csv", "positions.csv", "summary.csv", "trades.csv"],
        )

    def test_risk_block_can_flatten_but_cannot_open_exposure(self):
        frame = historical_frame()
        frame.loc[2, "risk_allowed"] = 0
        frame.loc[2, "risk_block_reason"] = "portfolio:net_dv01_limit"
        frame.loc[2, "swap_futures_contracts_rounded_2y"] = 0
        frame.loc[2, "treasury_futures_contracts_rounded_2y"] = 0
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=frame):
                result, _ = run_historical_backtest("risk-flatten", Path(directory))
        self.assertTrue(any(decision.reason_code == "risk_flatten" for decision in result.decisions))
        self.assertFalse(any(fill.remaining_quantity_contracts for fill in result.fills))
        self.assertEqual(dict(result.manifest)["risk_blocked_days"], "1")

    def test_roll_uses_explicit_zero_return_retiring_mark_policy(self):
        frame = historical_frame()
        frame.loc[2:, "swap_ticker_2y"] = "YITM24"
        frame.loc[2:, "swap_price_2y"] = 125.0
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=frame):
                result, _ = run_historical_backtest("roll", Path(directory))
        self.assertEqual(
            dict(result.manifest)["historical_roll_mark_policy"],
            "last_pre_roll_mark_zero_return",
        )
        self.assertTrue(any("roll" in decision.reason_code for decision in result.decisions))
```

Use a separate five-event zero-cost fixture with a full holding interval and assert the hand calculation:

```python
self.assertEqual(result.daily[3].gross_pnl_usd, D("220.0"))
self.assertEqual(result.daily[3].net_pnl_usd, D("220.0"))
self.assertEqual(result.daily[3].equity_usd, D("1000220.0"))
```

- [ ] **Step 2: Run the focused tests and observe RED**

Run:

```powershell
& $python -m unittest docs.tests.test_historical_backtest.HistoricalRunTests -v
```

Expected: import error for `run_historical_backtest` or assertion failures because no historical strategy/orchestrator exists.

- [ ] **Step 3: Implement the minimum historical strategy closure**

In `backtesting/historical.py`, add a private closure keyed by decision timestamp. It must use `snapshot.paper_positions` as the only current-position state and issue deltas to the row's desired quantities:

```python
def _signed_delta(side: OrderSide, quantity: int) -> int:
    return quantity if side is OrderSide.BUY else -quantity


def _side_and_quantity(delta: int) -> tuple[OrderSide, int]:
    return (OrderSide.BUY, delta) if delta > 0 else (OrderSide.SELL, -delta)


def _historical_strategy(run_id: str, frame: pd.DataFrame, assumptions: NaiveAssumptions):
    rows = {
        datetime.combine(row["date"].date(), time(21), tzinfo=UTC): row
        for _, row in frame.iterrows()
    }

    def strategy(snapshot: MarketSnapshot) -> StrategyResult:
        row = rows[snapshot.decision_time_utc]
        current = {item.instrument_id: item.quantity_contracts for item in snapshot.paper_positions}
        marks = {item.instrument_id: item.price_points for item in snapshot.instruments}
        decisions = []
        risk_decisions = []
        intents = []
        # For each maturity, desired quantities come only from the two rounded
        # contract columns. Close retiring tickers before opening current ones.
        # Emit no SignalDecision when every delta is zero.
        return StrategyResult(tuple(decisions), tuple(risk_decisions), tuple(intents))

    return strategy
```

Fill the loop with these exact policies:

- desired quantities are zero whenever upstream `risk_allowed != 1`;
- a blocked flat row returns `RiskDecision(False, Decimal("0"), reasons, False, FlattenUrgency.NONE, (), ())` and no intent;
- a blocked row with exposure returns an execution permission `RiskDecision(True, Decimal("0"), (*reasons, "flatten_only"), True, FlattenUrgency.SCHEDULED, (), ())`, emits only exposure-reducing close intents, and is still counted from the upstream frame in the final manifest;
- an allowed row returns `RiskDecision(True, Decimal("1"), ("within_limits",), False, FlattenUrgency.NONE, (), ())`;
- decision IDs are `historical-YYYY-MM-DD-<maturity-lower>`;
- reason codes are `historical_target_change`, `risk_flatten`, or `contract_roll`;
- `prior_state` comes from the sign of the held swap quantity, and `new_state`/`direction` come from the desired swap quantity;
- intents use `OrderType.MARKET`, `TimeInForce.DAY`, `paper_only=True`, the current mark as `reference_price_points`, `assumptions.slippage_points` as `max_slippage_price_points`, current time as earliest/activation, and current time plus seven calendar days as expiry;
- a blocked row may never create an intent whose resulting absolute instrument position exceeds its current absolute position.

For a ticker change, `_events_from_frame` must retain the retiring ticker's last observed price/DV01/multiplier on the roll-decision event and its following fill event only. Tag those observations `historical_roll_zero_return_proxy`, reject conflicting real observations, and never apply this carry rule to an unchanged ticker or ordinary missing mark.

- [ ] **Step 4: Implement the public historical orchestrator**

Add the exact public function and manifest correction:

```python
from dataclasses import replace
from pathlib import Path

from backtesting.assumptions import NAIVE_ASSUMPTIONS, NaiveAssumptions
from backtesting.engine import BacktestResult, run_backtest
from backtesting.reports import write_results


def _upsert(items: tuple[tuple[str, str], ...], key: str, value: str):
    output = [(name, value if name == key else old) for name, old in items]
    if not any(name == key for name, _ in items):
        output.append((key, value))
    return tuple(output)


def run_historical_backtest(
    run_id: str,
    output_root: Path,
    start: str = "auto",
    end: str = "auto",
    refresh_signals: bool = False,
    assumptions: NaiveAssumptions = NAIVE_ASSUMPTIONS,
    initial_equity_usd: Decimal = Decimal("1000000"),
) -> tuple[BacktestResult, Path]:
    frame = _load_historical_frame(refresh_signals)
    start_date = frame["date"].min().date() if start == "auto" else pd.Timestamp(start).date()
    end_date = frame["date"].max().date() if end == "auto" else pd.Timestamp(end).date()
    if start_date > end_date:
        raise RuntimeError(f"Backtest start is after end: {start_date} > {end_date}")
    selected = frame[frame["date"].dt.date.between(start_date, end_date)].reset_index(drop=True)
    if selected.empty:
        raise RuntimeError(f"No rows found from {start} to {end}.")
    result = run_backtest(
        run_id,
        _events_from_frame(selected),
        _historical_strategy(run_id, selected, assumptions),
        assumptions,
        initial_equity_usd,
    )
    risk_allowed = (
        selected["risk_allowed"]
        if "risk_allowed" in selected
        else pd.Series(1, index=selected.index)
    )
    blocked_days = int(risk_allowed.ne(1).sum())
    result = replace(
        result,
        manifest=_upsert(result.manifest, "risk_blocked_days", str(blocked_days)) + (
            ("historical_input_mode", "legacy_signal_risk_adapter"),
            ("historical_roll_mark_policy", "last_pre_roll_mark_zero_return"),
        ),
        summary=_upsert(result.summary, "risk_blocked_days", str(blocked_days)),
    )
    return result, write_results(result, output_root)
```

Validate `output_root` as `Path`, catch invalid date text and raise `RuntimeError("Invalid backtest window: ...")`, and reject duplicate manifest keys before writing.

Export `run_historical_backtest` from `backtesting/__init__.py` and add it to `__all__`.

- [ ] **Step 5: Run adapter and existing replay tests**

Run:

```powershell
& $python -m unittest docs.tests.test_historical_backtest -v
& $python -m unittest docs.tests.test_naive_backtest -v
```

Expected: the new historical suite and all 16 existing replay/report tests pass.

- [ ] **Step 6: Commit the canonical historical runner**

```powershell
git add backtesting/historical.py backtesting/__init__.py docs/tests/test_historical_backtest.py
git diff --cached --check
git commit -m "feat: run historical data through causal backtesting"
```

---

### Task 3: Add the single supported CLI and offline self-check

**Files:**
- Create: `backtesting/__main__.py`
- Modify: `docs/tests/test_historical_backtest.py`

**Interfaces:**
- Consumes: `run_historical_backtest`, `NaiveAssumptions`, and `config.DATA_DIR`.
- Produces: `parse_args(argv: list[str] | None = None) -> argparse.Namespace`, `self_check() -> None`, and `main(argv: list[str] | None = None) -> int` used by `python -m backtesting`.

- [ ] **Step 1: Write failing CLI parsing, dispatch, and self-check tests**

Add:

```python
from contextlib import redirect_stdout
from io import StringIO

from backtesting.__main__ import main, parse_args


class HistoricalCliTests(unittest.TestCase):
    def test_cli_parses_decimal_costs_and_dispatches_once(self):
        args = parse_args([
            "--run-id", "cli-run",
            "--start", "2024-01-02",
            "--end", "2024-01-05",
            "--commission-usd-per-contract", "1.25",
        ])
        self.assertEqual(args.commission_usd_per_contract, D("1.25"))
        with patch("backtesting.__main__.run_historical_backtest") as run:
            run.return_value = (Mock(summary=()), Path("out/cli-run"))
            self.assertEqual(main(["--run-id", "cli-run"]), 0)
        run.assert_called_once()

    def test_self_check_is_offline_and_passes(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--self-check"]), 0)
        self.assertIn("[OK] backtesting self-check passed", output.getvalue())
```

- [ ] **Step 2: Run the CLI tests and observe RED**

Run:

```powershell
& $python -m unittest docs.tests.test_historical_backtest.HistoricalCliTests -v
```

Expected: import error because `backtesting.__main__` does not exist.

- [ ] **Step 3: Implement the minimal CLI**

Create `backtesting/__main__.py`:

```python
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory

from config import DATA_DIR

from .assumptions import NAIVE_ASSUMPTIONS, NaiveAssumptions
from .historical import run_historical_backtest


def _decimal(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("must be a decimal") from error
    if not value.is_finite() or value < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative decimal")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the canonical swap-arbitrage backtest.")
    parser.add_argument("--run-id", default="historical-backtest")
    parser.add_argument("--start", default="auto")
    parser.add_argument("--end", default="auto")
    parser.add_argument("--initial-equity", type=_decimal, default=Decimal("1000000"))
    parser.add_argument("--output-root", type=Path, default=DATA_DIR / "results" / "backtests")
    parser.add_argument("--refresh-signals", action="store_true")
    parser.add_argument("--bid-ask-half-spread-points", type=_decimal, default=NAIVE_ASSUMPTIONS.bid_ask_half_spread_points)
    parser.add_argument("--commission-usd-per-contract", type=_decimal, default=NAIVE_ASSUMPTIONS.commission_usd_per_contract)
    parser.add_argument("--slippage-points", type=_decimal, default=NAIVE_ASSUMPTIONS.slippage_points)
    parser.add_argument("--financing-usd-per-contract-day", type=_decimal, default=NAIVE_ASSUMPTIONS.financing_usd_per_contract_day)
    parser.add_argument("--roll-usd-per-contract", type=_decimal, default=NAIVE_ASSUMPTIONS.roll_usd_per_contract)
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args(argv)
```

Implement the offline self-check with the Task 2 five-event fixture kept local
to `backtesting.historical`, so the runtime never imports a test module:

```python
def self_check() -> None:
    from unittest.mock import patch

    from .historical import _self_check_frame

    zero_costs = NaiveAssumptions(
        Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
    )
    with TemporaryDirectory() as directory:
        with patch(
            "backtesting.historical._load_historical_frame",
            return_value=_self_check_frame(),
        ):
            result, run_dir = run_historical_backtest(
                "self-check", Path(directory), assumptions=zero_costs
            )
        if len(list(run_dir.iterdir())) != 8:
            raise RuntimeError("self-check did not write eight canonical files")
        if result.daily[3].gross_pnl_usd != Decimal("220.0"):
            raise RuntimeError("self-check P&L reconciliation failed")
    print("[OK] backtesting self-check passed")
```

Add `_self_check_frame() -> pd.DataFrame` to `backtesting.historical`; it
returns only the fixed five-row synthetic fixture and performs no I/O.

`main` returns `0`, constructs `NaiveAssumptions` from the five flags, calls `run_historical_backtest` once, and prints only the run directory plus the existing summary key/value pairs. Do not create another summary calculation.

- [ ] **Step 4: Run CLI tests and the real module self-check**

Run:

```powershell
& $python -m unittest docs.tests.test_historical_backtest.HistoricalCliTests -v
& $python -m backtesting --self-check
```

Expected: tests pass and the module prints `[OK] backtesting self-check passed` with exit code `0`.

- [ ] **Step 5: Commit the one supported CLI**

```powershell
git add backtesting/__main__.py docs/tests/test_historical_backtest.py
git diff --cached --check
git commit -m "feat: add canonical backtesting CLI"
```

---

### Task 4: Remove the legacy engine and migrate its maintained checks

**Files:**
- Delete: `backtest_engine.py`
- Modify: `docs/tests/test_dv01_pipeline.py`
- Modify: `docs/tests/test_import_smoke.py`
- Modify: `docs/tests/test_historical_backtest.py`

**Interfaces:**
- Consumes: passing Tasks 1-3 tests and the `python -m backtesting` entry point.
- Produces: no maintained runtime import, test, or command dependency on `backtest_engine.py`.

- [ ] **Step 1: Confirm each legacy behavior has a replacement assertion**

Before deletion, map the six `BacktestMasterTests` cases exactly:

```text
active_range_uses_rounded_swap_contracts -> historical frame active-range test
pnl_uses_contract_quantities -> causal five-event hand reconciliation
nonzero_contracts_require_positive_master_dv01 -> active-field validation test
prior_exposure_requires_current_market_mark -> held-position missing-mark manifest test
contract_roll_uses_zero_return_and_close_open_turnover -> explicit roll-policy test
filtered_backtest_rebases_equity_to_requested_window -> start-flat window test
```

Add any missing assertion to `docs/tests/test_historical_backtest.py` before removing its legacy counterpart. The start-flat test must call `run_historical_backtest(start="2024-01-03", end="2024-01-05")` and assert first-row equity starts from the requested `initial_equity_usd`.

- [ ] **Step 2: Run both old and new focused suites before deletion**

Run:

```powershell
& $python -m unittest docs.tests.test_dv01_pipeline.BacktestMasterTests -v
& $python -m unittest docs.tests.test_historical_backtest -v
```

Expected: both suites pass, proving every maintained legacy behavior has a replacement test before deletion.

- [ ] **Step 3: Remove legacy imports, tests, and file**

In `docs/tests/test_dv01_pipeline.py`, remove:

```python
from backtest_engine import BacktestConfig, add_backtest_pnl, first_active_range, run_backtest
```

Delete the entire `BacktestMasterTests` class and remove imports used only by that class (`tempfile`, `Path`, or `patch` only when no remaining test uses them).

In `docs/tests/test_import_smoke.py`, replace:

```python
import backtest_engine  # noqa: F401
```

with:

```python
import backtesting  # noqa: F401
import backtesting.__main__  # noqa: F401
```

Delete `backtest_engine.py`. Do not leave a wrapper.

- [ ] **Step 4: Prove the maintained codebase no longer references the legacy entry point**

Run:

```powershell
rg -n "(?:import backtest_engine|from backtest_engine|python backtest_engine\.py)" README.md backtesting strategy data_pipeline agents docs/tests docs/TECHNICAL_DOCUMENTATION.md docs/FILE_MAP.md docs/FUNCTION_INVENTORY.md
```

Expected at this stage: documentation references may remain for Task 5, but no Python runtime or test reference remains.

- [ ] **Step 5: Run the migrated tests after deletion**

Run:

```powershell
& $python -m unittest docs.tests.test_historical_backtest -v
& $python -m unittest docs.tests.test_naive_backtest -v
& $python -m unittest docs.tests.test_dv01_pipeline -v
```

Expected: all three modules pass with no `backtest_engine` import.

- [ ] **Step 6: Commit the deletion**

```powershell
git add backtest_engine.py docs/tests/test_dv01_pipeline.py docs/tests/test_import_smoke.py docs/tests/test_historical_backtest.py
git diff --cached --check
git commit -m "refactor: remove legacy backtest engine"
```

---

### Task 5: Publish one documented backtesting workflow and verify the repository

**Files:**
- Modify: `README.md`
- Modify: `docs/TECHNICAL_DOCUMENTATION.md`
- Modify: `docs/FILE_MAP.md`
- Modify: `docs/FUNCTION_INVENTORY.md`
- Modify: `docs/verification/P40.md` only if its current-status section claims two maintained runners; retain historical evidence verbatim.

**Interfaces:**
- Consumes: final public signatures from Tasks 1-3 and deletion from Task 4.
- Produces: one documented command, one architecture path, an accurate file map, and an accurate public function inventory.

- [ ] **Step 1: Update README with the single command**

Replace maintained root-engine instructions with:

```markdown
The supported historical backtest command is:

```powershell
python -m backtesting --start auto --end auto
```

Each run writes the validated canonical report set under
`data/results/backtests/<run-id>/`. Use `--refresh-signals` only when the
upstream signal/risk data should be rebuilt.
```

Keep the paper-only and offline-test boundaries unchanged.

- [ ] **Step 2: Update the technical documentation**

Replace the two backtest paths with this exact maintained flow:

```text
historical_data_builder -> signal_pipeline -> risk_pipeline
    -> backtesting.historical -> backtesting.engine -> backtesting.reports
    -> data/results/backtests/<run-id>/{manifest,daily,decisions,orders,fills,trades,positions,summary}.csv
```

Document the causal delayed-fill rule, `start_flat` windows, the explicit
`last_pre_roll_mark_zero_return` research proxy, canonical outputs, CLI flags,
and the current limitation that canonical CSV-to-shared-strategy adaptation
and realistic executable roll/liquidity calibration remain incomplete.
Remove maintained `python backtest_engine.py` commands and descriptions.

- [ ] **Step 3: Update the file map**

Remove the root `backtest_engine.py` tree/table entry. Add:

```markdown
| `backtesting/historical.py` | Adapts existing historical signal/risk output into causal replay events and writes canonical results. |
| `backtesting/__main__.py` | Provides the single `python -m backtesting` CLI and offline self-check. |
```

State that `backtesting/engine.py` is the sole simulation/accounting engine.

- [ ] **Step 4: Update the function inventory**

Delete the entire `backtest_engine.py` function section. Under `backtesting/`, add:

```markdown
### `backtesting/historical.py`

- `run_historical_backtest(run_id: str, output_root: Path, start: str = "auto", end: str = "auto", refresh_signals: bool = False, assumptions: NaiveAssumptions = NAIVE_ASSUMPTIONS, initial_equity_usd: Decimal = Decimal("1000000"))` → **Output:** `tuple[BacktestResult, Path]`

### `backtesting/__main__.py`

- `parse_args(argv: list[str] | None = None)` → **Output:** `argparse.Namespace`
- `self_check()` → **Output:** `None`
- `main(argv: list[str] | None = None)` → **Output:** `int`
```

Do not inventory private conversion helpers.

- [ ] **Step 5: Run documentation/reference checks**

Run:

```powershell
rg -n "(?:import backtest_engine|from backtest_engine|python backtest_engine\.py)" README.md backtesting strategy data_pipeline agents docs/tests docs/TECHNICAL_DOCUMENTATION.md docs/FILE_MAP.md docs/FUNCTION_INVENTORY.md
rg -n "python -m backtesting|run_historical_backtest|backtesting/historical.py" README.md docs/TECHNICAL_DOCUMENTATION.md docs/FILE_MAP.md docs/FUNCTION_INVENTORY.md
```

Expected: the first command returns no maintained references; the second finds the new command/API in every required document.

- [ ] **Step 6: Run fresh full verification**

Set the bundled Python once without repurposing a system variable:

```powershell
$python = 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
```

Run:

```powershell
& $python -m unittest docs.tests.test_historical_backtest -v
& $python -m unittest docs.tests.test_naive_backtest -v
& $python -m unittest discover -s docs/tests -v
& $python -m unittest discover -s agents/agent_0/tests -v
& $python -m compileall -q backtesting strategy data_pipeline agents/agent_0 docs/tests
& $python signal_pipeline.py --self-check
& $python risk_pipeline.py --self-check
& $python -m backtesting --self-check
git diff --check
```

Expected: every command exits `0`. If the bundled Python lacks declared
`ib_insync`, rerun the two full discovery commands with the existing `.venv`
site-packages added temporarily; do not install or modify dependencies:

```powershell
$previousPythonPath = $env:PYTHONPATH
$projectSitePackages = (Resolve-Path '.venv\Lib\site-packages').Path
$env:PYTHONPATH = if ($previousPythonPath) { "$projectSitePackages;$previousPythonPath" } else { $projectSitePackages }
try {
    & $python -m unittest discover -s docs/tests -v
    & $python -m unittest discover -s agents/agent_0/tests -v
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
```

- [ ] **Step 7: Inspect final scope and commit documentation**

Before staging, compare `git status --short` with the baseline recorded at
execution start. Preserve unrelated user changes. For already-dirty
`README.md` and `docs/TECHNICAL_DOCUMENTATION.md`, stage only migration-owned
hunks; stage the clean file-map/function-inventory files normally.

```powershell
git diff -- README.md docs/TECHNICAL_DOCUMENTATION.md docs/FILE_MAP.md docs/FUNCTION_INVENTORY.md
git add -p -- README.md docs/TECHNICAL_DOCUMENTATION.md
git add -- docs/FILE_MAP.md docs/FUNCTION_INVENTORY.md
git diff --cached --name-status
git diff --check
git commit -m "docs: document canonical backtesting workflow"
```

The final commit must contain only the intended documentation updates and no
unrelated Agent 0, master-plan, audit, or pre-existing backtesting changes.
