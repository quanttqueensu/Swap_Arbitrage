> **Superseded and security-redacted (2026-07-29):** This is a historical
> implementation plan. Its initial 50-orders/day (250/week) and later
> 20-orders/day (100/week) proposals are superseded by MG1's authoritative
> 5-orders/day (25/week) selection. One historical paper-account identifier
> was replaced with `DU_REDACTED`; the historical body is otherwise unchanged.

# Agent 0 Weekly Orders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one invocation of Agent 0 queue 250 random paper-market orders at IBKR for the next calendar week without requiring Python or Trader Workstation to remain open.

**Architecture:** Generate and persist a full weekly plan before submission, then transmit each missing order with an IBKR `GoodAfterTime`. Two CSV files under `agents/agent_0/orders/` are the only local tracking mechanism; deterministic order references plus IBKR open-order reconciliation make reruns resumable.

**Tech Stack:** Python standard library (`csv`, `datetime`, `random`, `unittest`, `zoneinfo`), existing `ib_insync`, existing pandas-based sizing loader.

## Global Constraints

- Preserve `PAPER_ONLY = True`, `LIVE_TRADING_ENABLED = False`, port `7497`, and the `DU` account-prefix check.
- Queue exactly 50 orders for each Monday-through-Friday date in the next calendar week.
- Use random activation times from 09:00 through 15:00 `America/New_York`.
- Use only transmitted `MKT`/`DAY` orders with `GoodAfterTime`.
- Choose instruments uniformly from positive sizing caps, sides independently at 50/50, and quantities uniformly from 1 through the chosen cap.
- Never skip, flatten, inspect positions, or change decisions based on exposure.
- Do not connect to IBKR from tests.
- Do not overwrite or discard pre-existing uncommitted changes. In particular, `agents/agent_0/config.py` and `agents/agent_0/SETTINGS.md` already contain user edits, so implementation changes remain uncommitted for user review.

---

### Task 1: Weekly plan generation

**Files:**
- Modify: `agents/agent_0/models.py`
- Modify: `agents/agent_0/random_policy.py`
- Create: `tests/__init__.py`
- Create: `tests/test_agent_0_weekly_orders.py`

**Interfaces:**
- Consumes: `dict[str, SizingCap]`, a `date`, and the existing `AGENT0_RANDOM_SEED` environment convention.
- Produces: `QueuedOrder`, `next_weekdays(today) -> list[date]`, and `RandomPolicy.build_week_plan(sizing_caps, today) -> list[QueuedOrder]`.

- [ ] **Step 1: Write failing schedule and random-plan tests**

```python
from collections import Counter
from datetime import date, time
import unittest

from agents.agent_0.models import AgentInstrument, SizingCap
from agents.agent_0.random_policy import RandomPolicy, next_weekdays


class WeeklyPlanTests(unittest.TestCase):
    def setUp(self):
        instrument = AgentInstrument("2Y", "ZT", "treasury_future")
        self.caps = {
            "ZT": SizingCap(instrument, main_quantity=100, max_agent_quantity=10, source="test")
        }

    def test_next_weekdays_are_next_calendar_monday_through_friday(self):
        self.assertEqual(
            next_weekdays(date(2026, 7, 15)),
            [date(2026, 7, day) for day in range(20, 25)],
        )

    def test_week_plan_has_fifty_valid_orders_per_day(self):
        plan = RandomPolicy(seed="test").build_week_plan(self.caps, date(2026, 7, 15))
        counts = Counter(order.activate_at.date() for order in plan)
        self.assertEqual(len(plan), 250)
        self.assertEqual(set(counts.values()), {50})
        self.assertTrue(all(time(9) <= item.activate_at.time() <= time(15) for item in plan))
        self.assertTrue({item.side for item in plan} <= {"BUY", "SELL"})
        self.assertEqual({item.side for item in plan}, {"BUY", "SELL"})
        self.assertTrue(all(1 <= item.quantity <= 10 for item in plan))
        self.assertEqual(len({item.order_ref for item in plan}), 250)

    def test_week_plan_requires_a_positive_sizing_cap(self):
        with self.assertRaisesRegex(RuntimeError, "positive sizing cap"):
            RandomPolicy(seed="test").build_week_plan({}, date(2026, 7, 15))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.WeeklyPlanTests -v`

Expected: import failure because `QueuedOrder`, `next_weekdays`, and `build_week_plan` do not exist.

- [ ] **Step 3: Implement the minimal model and generator**

Add this data shape to `models.py`:

```python
@dataclass
class QueuedOrder:
    order_ref: str
    activate_at: datetime
    symbol: str
    side: OrderSide
    quantity: int
    status: str = "planned"
    contract_id: str = ""
    order_id: str = ""
```

Replace the flatten/skip policy with:

```python
def next_weekdays(today: date) -> list[date]:
    next_monday = today + timedelta(days=7 - today.weekday())
    return [next_monday + timedelta(days=offset) for offset in range(5)]


class RandomPolicy:
    def __init__(self, seed: str | None = None) -> None:
        self.rng = random.Random(seed if seed is not None else os.getenv(config.RANDOM_SEED_ENV_VAR))

    def build_week_plan(self, sizing_caps: dict[str, SizingCap], today: date) -> list[QueuedOrder]:
        eligible = [cap for cap in sizing_caps.values() if cap.max_agent_quantity > 0]
        if not eligible:
            raise RuntimeError("No instrument has a positive sizing cap.")

        zone = ZoneInfo(config.ACTIVATION_TIMEZONE)
        plan = []
        for day in next_weekdays(today):
            for sequence in range(1, config.ORDERS_PER_DAY + 1):
                cap = self.rng.choice(eligible)
                seconds = self.rng.randrange(config.ACTIVATION_START_HOUR * 3600, config.ACTIVATION_END_HOUR * 3600 + 1)
                plan.append(QueuedOrder(
                    order_ref=f"{config.ORDER_REF_PREFIX}-{day:%Y%m%d}-{sequence:02d}",
                    activate_at=datetime.combine(day, time(), zone) + timedelta(seconds=seconds),
                    symbol=cap.instrument.symbol,
                    side=self.rng.choice(["BUY", "SELL"]),
                    quantity=self.rng.randint(1, cap.max_agent_quantity),
                ))
        return plan
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.WeeklyPlanTests -v`

Expected: 3 tests pass.

- [ ] **Step 5: Inspect the focused diff**

Run: `git diff --check -- agents/agent_0/models.py agents/agent_0/random_policy.py tests`

Expected: no whitespace errors. Do not commit yet because later tasks form one user-reviewable workflow.

---

### Task 2: IBKR order construction and CSV tracking

**Files:**
- Modify: `agents/agent_0/orders.py`
- Modify: `agents/agent_0/config.py`
- Modify: `tests/test_agent_0_weekly_orders.py`

**Interfaces:**
- Consumes: `QueuedOrder`, account ID, and timezone-aware current time.
- Produces: `build_order(account_id, queued_order)`, `load_orders(path)`, `save_orders(path, rows)`, and `roll_tracking(now)`.

- [ ] **Step 1: Write failing order-field and persistence tests**

```python
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.agent_0.models import QueuedOrder
from agents.agent_0.orders import build_order, load_orders, roll_tracking, save_orders


class OrderAndTrackingTests(unittest.TestCase):
    def sample(self, **changes):
        values = dict(
            order_ref="agent_0-20260720-01",
            activate_at=datetime(2026, 7, 20, 14, tzinfo=timezone.utc),
            symbol="ZT",
            side="BUY",
            quantity=2,
        )
        values.update(changes)
        return QueuedOrder(**values)

    def test_build_order_sets_server_scheduling_fields(self):
        order = build_order("DU123", self.sample())
        self.assertEqual(order.orderType, "MKT")
        self.assertEqual(order.tif, "DAY")
        self.assertEqual(order.account, "DU123")
        self.assertEqual(order.orderRef, "agent_0-20260720-01")
        self.assertEqual(order.goodAfterTime, "20260720-14:00:00")
        self.assertTrue(order.transmit)

    def test_tracking_round_trip_and_roll(self):
        with TemporaryDirectory() as directory:
            upcoming = Path(directory) / "upcoming.csv"
            previous = Path(directory) / "previous.csv"
            rows = [
                self.sample(order_ref="past", activate_at=datetime(2026, 7, 20, tzinfo=timezone.utc)),
                self.sample(order_ref="future", activate_at=datetime(2026, 7, 22, tzinfo=timezone.utc)),
            ]
            save_orders(upcoming, rows)
            roll_tracking(datetime(2026, 7, 21, tzinfo=timezone.utc), upcoming, previous)
            self.assertEqual([row.order_ref for row in load_orders(upcoming)], ["future"])
            self.assertEqual([row.order_ref for row in load_orders(previous)], ["past"])
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.OrderAndTrackingTests -v`

Expected: import/signature failures for the new tracking functions and scheduled-order builder.

- [ ] **Step 3: Add only the required configuration**

Replace log/state settings with:

```python
ORDERS_DIR = AGENT_DIR / "orders"
UPCOMING_ORDERS_FILE = ORDERS_DIR / "upcoming.csv"
PREVIOUS_ORDERS_FILE = ORDERS_DIR / "previous.csv"
ORDERS_PER_DAY = 50
ACTIVATION_TIMEZONE = "America/New_York"
ACTIVATION_START_HOUR = 9
ACTIVATION_END_HOUR = 15
SUBMISSION_WAIT_SECONDS = 0.25
```

Make `ensure_agent_directories()` create only `ORDERS_DIR`, remove daily-cap,
flattening, skip-weight, loop, and obsolete state settings, and expose the new
values from `settings_summary()`.

- [ ] **Step 4: Implement scheduled order construction and atomic CSV storage**

`build_order` validates the record, creates the existing `MarketOrder`, sets
`account`, `tif`, `transmit = True`, deterministic `orderRef`, and UTC
`goodAfterTime` in `YYYYMMDD-HH:MM:SS` form. CSV storage uses `csv.DictWriter`,
`datetime.isoformat()`, a sibling `.tmp` file, and `Path.replace()`; no new
dependency or logging layer is added. `roll_tracking` partitions on
`activate_at <= now` and appends past rows to existing previous rows.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.OrderAndTrackingTests -v`

Expected: 2 tests pass.

- [ ] **Step 6: Run all current tests**

Run: `.venv\Scripts\python.exe -m unittest discover -v`

Expected: all 5 tests pass.

---

### Task 3: Resumable weekly submission workflow

**Files:**
- Modify: `agents/agent_0/broker.py`
- Modify: `agents/agent_0/run.py`
- Modify: `tests/test_agent_0_weekly_orders.py`
- Delete: `agents/agent_0/state.py`
- Delete: `agents/agent_0/risk_limits.py`

**Interfaces:**
- Consumes: persisted plan records, IBKR open trades, resolved contracts, and `submit_order`.
- Produces: `open_orders_by_ref(ib) -> dict[str, Any]`, `submit_plan(ib, account_id, records, upcoming_path, previous_path) -> dict[str, int]`, and `queue_next_week(account_id=None) -> dict[str, int]`.

- [ ] **Step 1: Write failing duplicate and rejection workflow tests**

Use `unittest.mock.patch` only at the external IBKR boundary. Provide complete
fake trade structures containing `order.orderId`, `order.orderRef`,
`orderStatus.status`, and `log`. Assert that a matching server reference is not
submitted and an `Inactive` result moves to previous and prevents later
submissions.

```python
from types import SimpleNamespace
from unittest.mock import patch

from agents.agent_0.run import submit_plan


class FakeIB:
    def __init__(self, open_trades=()):
        self.open_trades = list(open_trades)

    def reqAllOpenOrders(self):
        return self.open_trades


def fake_trade(order_ref, status, order_id=1):
    return SimpleNamespace(
        order=SimpleNamespace(orderRef=order_ref, orderId=order_id),
        orderStatus=SimpleNamespace(status=status),
        log=[],
    )


class WorkflowTests(unittest.TestCase):
    def sample(self, **changes):
        values = dict(
            order_ref="agent_0-20260720-01",
            activate_at=datetime(2026, 7, 20, 14, tzinfo=timezone.utc),
            symbol="ZT",
            side="BUY",
            quantity=2,
        )
        values.update(changes)
        return QueuedOrder(**values)

    @patch("agents.agent_0.run.submit_order")
    def test_open_server_reference_is_not_submitted(self, mocked_submit):
        record = self.sample()
        with TemporaryDirectory() as directory:
            upcoming = Path(directory) / "upcoming.csv"
            previous = Path(directory) / "previous.csv"
            save_orders(upcoming, [record])
            ib = FakeIB([fake_trade(record.order_ref, "PreSubmitted", 42)])

            summary = submit_plan(ib, "DU123", [record], upcoming, previous)

            mocked_submit.assert_not_called()
            self.assertEqual(summary["already_submitted"], 1)
            saved = load_orders(upcoming)
            self.assertEqual(saved[0].status, "accepted")
            self.assertEqual(saved[0].order_id, "42")

    @patch("agents.agent_0.run.resolve_front_future")
    @patch("agents.agent_0.run.build_order")
    @patch("agents.agent_0.run.submit_order")
    def test_rejection_stops_later_submissions(
        self,
        mocked_submit,
        mocked_build,
        mocked_resolve,
    ):
        first = self.sample(order_ref="first")
        second = self.sample(order_ref="second")
        mocked_resolve.return_value = SimpleNamespace(conId=123)
        mocked_build.return_value = SimpleNamespace()
        mocked_submit.return_value = fake_trade("first", "Inactive", 7)

        with TemporaryDirectory() as directory:
            upcoming = Path(directory) / "upcoming.csv"
            previous = Path(directory) / "previous.csv"
            save_orders(upcoming, [first, second])

            summary = submit_plan(
                FakeIB(),
                "DU123",
                [first, second],
                upcoming,
                previous,
            )

            self.assertEqual(mocked_submit.call_count, 1)
            self.assertEqual(summary["rejected"], 1)
            self.assertEqual([row.order_ref for row in load_orders(previous)], ["first"])
            self.assertEqual([row.order_ref for row in load_orders(upcoming)], ["second"])
```

- [ ] **Step 2: Run workflow tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.WorkflowTests -v`

Expected: failures because `open_orders_by_ref`, `submit_plan`, and the weekly orchestrator do not exist.

- [ ] **Step 3: Implement the broker boundary**

Remove position-loading code. Add:

```python
def open_orders_by_ref(ib: Any) -> dict[str, Any]:
    return {
        str(trade.order.orderRef): trade
        for trade in ib.reqAllOpenOrders()
        if getattr(getattr(trade, "order", None), "orderRef", "")
    }
```

Keep account validation in `submit_order`, wait only
`config.SUBMISSION_WAIT_SECONDS`, and return the trade for status inspection.

- [ ] **Step 4: Replace `run.py` with the weekly orchestrator**

Keep direct-script and module import support. The orchestrator must:

1. roll tracking;
2. load caps and generate the proposed plan;
3. preserve existing rows by deterministic reference and persist the merged plan;
4. connect once and reconcile server references;
5. resolve each selected symbol once;
6. submit only planned rows, persist each accepted result immediately, and stop on `Cancelled` or `Inactive`;
7. move rejected rows to previous;
8. always disconnect in `finally`;
9. expose only the optional `--account` CLI argument.

`main()` prints one summary line containing the planned, accepted,
already-submitted, and rejected counts. No loop, batch, sleep scheduler,
position lookup, flatten branch, daily state, or decision log remains.

- [ ] **Step 5: Delete obsolete modules and imports**

Delete `state.py` and `risk_limits.py`. Remove
`qualify_existing_future_for_order` if no remaining caller exists. Confirm with:

Run: `rg -n "AgentState|append_order_log|RiskLimit|run_loop|run_batch|load_allowed_positions|qualify_existing_future" agents/agent_0 -g '*.py'`

Expected: no matches outside historical prose being updated in Task 4.

- [ ] **Step 6: Run workflow tests and verify GREEN**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.WorkflowTests -v`

Expected: workflow tests pass without an IBKR connection.

- [ ] **Step 7: Run the complete test suite**

Run: `.venv\Scripts\python.exe -m unittest discover -v`

Expected: all tests pass.

---

### Task 4: User workflow documentation and final verification

**Files:**
- Modify: `agents/agent_0/SETTINGS.md`
- Review: all changed Agent 0 files and `tests/test_agent_0_weekly_orders.py`

**Interfaces:**
- Documents: `python agents/agent_0/run.py` and `AGENT0_IBKR_ACCOUNT`.
- Produces: no new runtime interface.

- [ ] **Step 1: Update settings documentation**

Document the 250-order next-week behavior, 09:00–15:00 ET random activation,
50/50 BUY/SELL probability, no flattening, `GoodAfterTime`, paper-only routing,
the two CSV files, duplicate-safe resume behavior, and the fact that local CSV
status refreshes only when `run.py` is run again.

The final run section must contain:

````markdown
## Run

Set the paper account once in PowerShell:

```powershell
$env:AGENT0_IBKR_ACCOUNT = "DU1234567"
```

With Trader Workstation or IB Gateway connected, queue next week's orders:

```powershell
.venv\Scripts\python.exe agents\agent_0\run.py
```

After the accepted summary prints, Python and Trader Workstation may close.
IBKR holds the transmitted orders. Local `orders/upcoming.csv` and
`orders/previous.csv` refresh the next time this command runs.
````

- [ ] **Step 2: Run the requested ponytail complexity review**

Review only the implementation diff. Delete obsolete helpers, options,
dependencies, and single-use abstractions. Report any remaining findings in
the required one-line format and finish with the net removable-line count.

- [ ] **Step 3: Run final automated verification**

Run: `.venv\Scripts\python.exe -m unittest discover -v`

Expected: all tests pass with no network access.

Run: `.venv\Scripts\python.exe -m compileall -q agents/agent_0 tests`

Expected: exit code 0.

Run: `git diff --check`

Expected: no whitespace errors in the implementation diff.

- [ ] **Step 4: Review scope and repository state**

Run: `git status --short` and `git diff -- agents/agent_0 tests`

Expected: requested Agent 0/test changes are present; unrelated user edits in
top-level strategy files are untouched. Do not transmit a smoke-test order and
do not commit implementation files that overlap pre-existing user changes.

---

### Task 5: Allocate across three expiries under IBKR's working-order cap

**Files:**
- Modify: `agents/agent_0/config.py`
- Modify: `agents/agent_0/contracts.py`
- Modify: `agents/agent_0/broker.py`
- Modify: `agents/agent_0/run.py`
- Modify: `agents/agent_0/SETTINGS.md`
- Modify: `tests/test_agent_0_weekly_orders.py`

**Interfaces:**
- Consumes: current IBKR open trades, the existing 250 `QueuedOrder` records, and contract details for each selected symbol.
- Produces: `resolve_futures(ib, instrument, count=3) -> list[Any]`, `working_order_counts(trades, account_id) -> Counter[tuple[str, str]]`, and `allocate_contracts(records, contracts_by_symbol, occupied) -> dict[str, Any]`.

- [ ] **Step 1: Write failing contract-selection and capacity-allocation tests**

```python
from collections import Counter
from datetime import timedelta

from agents.agent_0.contracts import pick_front_contracts
from agents.agent_0.run import allocate_contracts, working_order_counts


class CapacityTests(unittest.TestCase):
    def record(self, sequence, status="planned"):
        return QueuedOrder(
            order_ref=f"capacity-{sequence}",
            activate_at=datetime(2026, 7, 20, 14, tzinfo=timezone.utc),
            symbol="ZT",
            side="BUY",
            quantity=1,
            status=status,
        )

    def test_pick_front_contracts_returns_nearest_three_valid_expiries(self):
        today = date.today()
        details = [
            SimpleNamespace(contract=SimpleNamespace(
                conId=con_id,
                lastTradeDateOrContractMonth=(today + timedelta(days=days)).strftime("%Y%m%d"),
            ))
            for con_id, days in [(4, 60), (2, 30), (1, 10), (3, 45)]
        ]
        self.assertEqual(
            [item.conId for item in pick_front_contracts(details, 3)],
            [2, 3, 4],
        )

    def test_allocator_never_exceeds_fifteen_per_contract_side(self):
        contracts = [SimpleNamespace(conId=value) for value in (1, 2, 3)]
        records = [self.record(sequence) for sequence in range(45)]
        allocated = allocate_contracts(records, {"ZT": contracts}, Counter())
        counts = Counter((allocated[row.order_ref].conId, row.side) for row in records)
        self.assertEqual(set(counts.values()), {15})

    def test_existing_account_orders_reduce_available_capacity(self):
        trades = [
            SimpleNamespace(
                contract=SimpleNamespace(conId=1),
                order=SimpleNamespace(account="DU123", action="BUY"),
            )
            for _ in range(15)
        ]
        occupied = working_order_counts(trades, "DU123")
        record = self.record(1)
        contracts = [SimpleNamespace(conId=1), SimpleNamespace(conId=2), SimpleNamespace(conId=3)]
        allocated = allocate_contracts([record], {"ZT": contracts}, occupied)
        self.assertEqual(allocated[record.order_ref].conId, 2)

    def test_allocator_fails_before_submission_when_capacity_is_insufficient(self):
        contracts = [SimpleNamespace(conId=value) for value in (1, 2, 3)]
        records = [self.record(sequence) for sequence in range(46)]
        with self.assertRaisesRegex(RuntimeError, "working-order capacity"):
            allocate_contracts(records, {"ZT": contracts}, Counter())
```

- [ ] **Step 2: Write the failing server-authority regression test**

```python
@patch("agents.agent_0.run.resolve_futures")
@patch("agents.agent_0.run.build_order")
@patch("agents.agent_0.run.submit_order")
def test_local_accepted_order_missing_from_ibkr_is_resubmitted(
    self,
    mocked_submit,
    mocked_build,
    mocked_resolve,
):
    record = self.sample(order_ref="erased", status="accepted", order_id="77")
    mocked_resolve.return_value = [SimpleNamespace(conId=value) for value in (1, 2, 3)]
    mocked_build.return_value = SimpleNamespace()
    mocked_submit.return_value = fake_trade("erased", "PreSubmitted", 88)
    with TemporaryDirectory() as directory:
        upcoming = Path(directory) / "upcoming.csv"
        previous = Path(directory) / "previous.csv"
        save_orders(upcoming, [record])
        summary = submit_plan(FakeIB(), "DU123", [record], upcoming, previous)
        self.assertEqual(mocked_submit.call_count, 1)
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(load_orders(upcoming)[0].order_id, "88")
```

- [ ] **Step 3: Run the new tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.CapacityTests tests.test_agent_0_weekly_orders.WorkflowTests.test_local_accepted_order_missing_from_ibkr_is_resubmitted -v`

Expected: import/patch failures because `pick_front_contracts`, `resolve_futures`, `allocate_contracts`, and `working_order_counts` do not exist.

- [ ] **Step 4: Implement three-expiry resolution**

Add `CONTRACTS_PER_SYMBOL = 3` and
`MAX_WORKING_ORDERS_PER_CONTRACT_SIDE = 15` to `config.py`.

Replace the single-front selection in `contracts.py` with:

```python
def pick_front_contracts(details: list[Any], count: int) -> list[Any]:
    earliest_expiry = date.today() + timedelta(days=config.MIN_DAYS_TO_EXPIRY)
    candidates = []
    for item in details:
        contract = item.contract
        expiry = parse_contract_month(getattr(contract, "lastTradeDateOrContractMonth", ""))
        if expiry is not None and expiry > earliest_expiry:
            candidates.append((expiry, contract))
    candidates.sort(key=lambda item: item[0])
    return [contract for _, contract in candidates[:count]]


def resolve_futures(ib: Any, instrument: AgentInstrument, count: int = config.CONTRACTS_PER_SYMBOL) -> list[Any]:
    for exchange in config.IBKR_EXCHANGES_TO_TRY:
        contract = Future(symbol=instrument.symbol, exchange=exchange, currency=instrument.currency)
        details = ib.reqContractDetails(contract)
        fronts = pick_front_contracts(details, count)
        if len(fronts) != count:
            continue
        qualified = ib.qualifyContracts(*fronts)
        if len(qualified) == count:
            return list(qualified)
    raise RuntimeError(f"Could not resolve {count} valid contracts for {instrument.symbol}.")
```

- [ ] **Step 5: Implement capacity preflight and server-authoritative reconciliation**

In `run.py`, request open trades once. Build `server_orders` by deterministic
reference, reset every local accepted record absent from that mapping to
`planned` with empty `order_id` and `contract_id`, and exclude only references
that still exist at IBKR from allocation.

Add the pure helpers:

```python
def working_order_counts(trades: list[Any], account_id: str) -> Counter:
    return Counter(
        (str(trade.contract.conId), str(trade.order.action))
        for trade in trades
        if getattr(trade.order, "account", "") == account_id
    )


def allocate_contracts(records, contracts_by_symbol, occupied):
    counts = Counter(occupied)
    allocated = {}
    for record in records:
        available = [
            contract for contract in contracts_by_symbol[record.symbol]
            if counts[(str(contract.conId), record.side)]
            < config.MAX_WORKING_ORDERS_PER_CONTRACT_SIDE
        ]
        if not available:
            raise RuntimeError(
                f"Insufficient IBKR working-order capacity for {record.symbol} {record.side}."
            )
        contract = min(
            available,
            key=lambda item: (counts[(str(item.conId), record.side)], str(item.conId)),
        )
        record.contract_id = str(contract.conId)
        allocated[record.order_ref] = contract
        counts[(record.contract_id, record.side)] += 1
    return allocated
```

Resolve exactly three contracts for each symbol used by unsubmitted records,
allocate the entire remaining plan, and save the allocation before the first
new call to `submit_order`. Remove the obsolete `open_orders_by_ref` broker
helper.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.CapacityTests tests.test_agent_0_weekly_orders.WorkflowTests -v`

Expected: all capacity and workflow tests pass without connecting to IBKR.

- [ ] **Step 7: Update settings and run full verification**

Document the three-expiry allocation, 15-per-contract-side cap, IBKR-authority
rule, and capacity preflight in `SETTINGS.md`.

Run: `.venv\Scripts\python.exe -m unittest discover -v`

Expected: all tests pass.

Run: `.venv\Scripts\python.exe -m compileall -q agents\agent_0 tests`

Expected: exit code 0.

Run: `git diff --check -- agents/agent_0 tests`

Expected: no whitespace errors. Do not connect to IBKR or submit orders during
verification.

### Task 6: Enforce a 10% IBKR Margin Reserve

**Files:**
- Modify: `agents/agent_0/config.py`
- Modify: `agents/agent_0/broker.py`
- Modify: `agents/agent_0/run.py`
- Modify: `agents/agent_0/SETTINGS.md`
- Test: `tests/test_agent_0_weekly_orders.py`

**Interfaces:**
- Produces: `margin_reserve_ok(order_state: Any) -> bool`
- Produces: `fit_order_to_margin(ib, account_id, contract, record) -> Any | None`

- [ ] **Step 1: Write failing margin tests**

Add tests proving a preview with `equityWithLoanAfter=100000` and
`initMarginAfter=90000` passes, `initMarginAfter=90001` fails, a planned
quantity is reduced to the largest passing quantity, and quantity 1 failure
returns `None` without calling `placeOrder`.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.MarginTests -v`

Expected: import failure because the margin helpers do not exist.

- [ ] **Step 3: Implement the minimal margin preview**

Set `MARGIN_RESERVE_FRACTION = Decimal("0.10")`. Parse IBKR's
`equityWithLoanAfter` and `initMarginAfter` with `Decimal`; fail closed on
missing or invalid preview values. In `fit_order_to_margin`, preview quantities
from the planned value down to 1 using `ib.whatIfOrder`, return the first order
that preserves the reserve, and update the tracked quantity only for that
passing order.

- [ ] **Step 4: Stop cleanly when quantity 1 cannot fit**

In `submit_plan`, call `fit_order_to_margin` immediately before
`submit_order`. If it returns `None`, leave that and later records planned,
set `margin_blocked` to the number remaining, persist tracking, and stop
without transmitting the blocked order.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.MarginTests tests.test_agent_0_weekly_orders.WorkflowTests -v`

Expected: all margin and workflow tests pass.

### Task 7: Add Explicit Global Cancellation

**Files:**
- Modify: `agents/agent_0/broker.py`
- Modify: `agents/agent_0/run.py`
- Modify: `agents/agent_0/SETTINGS.md`
- Test: `tests/test_agent_0_weekly_orders.py`

**Interfaces:**
- Produces: `cancel_all_orders(ib: Any) -> tuple[int, int]`
- Produces: CLI flag `--cancel-all`

- [ ] **Step 1: Write failing cancellation tests**

Add a fake IB object recording `reqGlobalCancel`, `sleep`, and
`reqAllOpenOrders`. Test that `cancel_all_orders` invokes global cancellation
once and waits until no orders remain. Test that the `--cancel-all` orchestration
resets accepted local rows to planned and does not call `queue_next_week`.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.CancellationTests -v`

Expected: import failure because `cancel_all_orders` does not exist.

- [ ] **Step 3: Implement native global cancellation**

Read current orders with `reqAllOpenOrders`, call `reqGlobalCancel()` once,
then poll with `ib.sleep(SUBMISSION_WAIT_SECONDS)` and `reqAllOpenOrders` until
empty or the existing IBKR timeout is reached. Return before/after counts and
raise if orders remain after the timeout.

- [ ] **Step 4: Add the isolated CLI path**

Add `--cancel-all` to `parse_args`. In `main`, connect, call the cancellation
orchestrator, reset accepted upcoming rows to planned with cleared IBKR IDs,
print `[ALL ORDERS CANCELLED] before=N remaining=0`, disconnect, and return
without generating or submitting a weekly plan.

- [ ] **Step 5: Run cancellation tests and verify GREEN**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.CancellationTests -v`

Expected: all cancellation tests pass without connecting to IBKR.

### Task 8: Final Verification and Approved Cancellation

**Files:**
- Modify: `agents/agent_0/SETTINGS.md`

- [ ] **Step 1: Run full local verification**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: all tests pass.

Run: `.venv\Scripts\python.exe -m compileall -q agents\agent_0`

Expected: exit code 0.

Run: `git diff --check -- agents/agent_0 tests`

Expected: no whitespace errors.

- [ ] **Step 2: Execute the user-approved destructive operation**

Run: `.venv\Scripts\python.exe agents\agent_0\run.py --cancel-all`

Expected: `[ALL ORDERS CANCELLED] before=N remaining=0`. Do not run the normal
weekly queue command afterward.

### Task 9: Reduce the Weekly Plan to 20 Orders per Day

**Files:**
- Modify: `agents/agent_0/config.py`
- Modify: `agents/agent_0/run.py`
- Modify: `agents/agent_0/SETTINGS.md`
- Test: `tests/test_agent_0_weekly_orders.py`

**Interfaces:**
- Produces: `ORDERS_PER_DAY = 20`
- Produces: `reconcile_plan(existing, proposed) -> list[QueuedOrder]`

- [ ] **Step 1: Write failing tests**

Change the seeded weekly-plan assertion to require 100 total orders and exactly
20 per weekday. Add a reconciliation test with one matching existing row and
one obsolete row; require only the proposed references in proposed order and
preserve the matching row's accepted state.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_agent_0_weekly_orders.WeeklyPlanTests -v`

Expected: the plan still contains 250 orders and `reconcile_plan` is missing.

- [ ] **Step 3: Implement the minimum reduction**

Set `ORDERS_PER_DAY = 20`. Add:

```python
def reconcile_plan(existing, proposed):
    existing_by_ref = {row.order_ref: row for row in existing}
    return [existing_by_ref.get(row.order_ref, row) for row in proposed]
```

Use this result in `queue_next_week` instead of appending proposed rows to all
existing rows. Update `SETTINGS.md` to state 20 per day and 100 total.

- [ ] **Step 4: Run full verification**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: all tests pass.

Run: `.venv\Scripts\python.exe -m compileall -q agents\agent_0`

Expected: exit code 0.

- [ ] **Step 5: Cancel the approved existing schedule**

Run: `.venv\Scripts\python.exe agents\agent_0\run.py --cancel-all --account DU_REDACTED`

Expected: `remaining=0`. Verify with a fresh read-only `reqAllOpenOrders` call.
Do not run the normal weekly queue afterward.
