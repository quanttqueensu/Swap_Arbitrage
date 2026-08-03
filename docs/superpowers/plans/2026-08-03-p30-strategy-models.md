# P30 Immutable Strategy Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the immutable, validated strategy records and stable CSV serialization boundary required by P30, without migrating behavior.

**Architecture:** Keep all strategy data contracts in one standard-library-only `strategy.models` module. Six public boundary records compose a few concrete frozen value records; module-level validation helpers enforce domains and `to_csv_row` provides deterministic serialization without I/O or schema coupling.

**Tech Stack:** Python 3.12 standard library (`dataclasses`, `datetime`, `decimal`, `enum`, `json`), `unittest`.

## Global Constraints

- Work directly on `main`; the user explicitly waived worktree isolation.
- Records are frozen and slotted; collection fields are normalized to tuples.
- `strategy.models` imports no project module, file API, clock, broker, or network client.
- Field names declare units with `_bps`, `_usd`, `_usd_per_bp`, `_price_points`, `_contracts`, or `_utc`.
- Every datetime is timezone-aware UTC and every Decimal is finite.
- `PositionState` and `TradeDirection` serialize as `-1`, `0`, or `1`.
- Absolute quantities, counts, costs, limits, slippage, gross DV01, and target DV01 are nonnegative; signed positions and residual net DV01 may be negative.
- `RiskDecision.scale` is in the inclusive range `[0, 1]`.
- `OrderIntent.paper_only` is the literal boolean `True`.
- Do not implement equations, rolling signals, broker adapters, data reads, or behavior migration.
- Review subagents request `gpt-5.6-luna` with high reasoning. If that runtime identifier is rejected, use the strongest available non-Sol reviewer model and record the substitution in the SDD ledger.

---

### Task 1: Immutable market input records and domains

**Files:**
- Create: `strategy/__init__.py`
- Create: `strategy/models.py`
- Create: `docs/tests/test_strategy_models.py`

**Interfaces:**
- Consumes: Python standard-library scalar values and iterables supplied by later adapters.
- Produces: `PositionState`, `TradeDirection`, `OrderSide`, `OrderType`, `TimeInForce`, `FlattenUrgency`, `NamedValue`, `RateObservation`, `InstrumentObservation`, `ContractMetadata`, `PaperPosition`, `WorkingOrder`, and `MarketSnapshot`.

- [ ] **Step 1: Write failing market-model tests**

Create tests importing the exact public names above. Build literals with
`UTC = timezone.utc` and assert the wished-for API:

```python
snapshot = MarketSnapshot(
    decision_time_utc=datetime(2026, 8, 3, 21, tzinfo=UTC),
    rates=[RateObservation("DGS2", "2Y", Decimal("410"), "UST",
                           datetime(2026, 8, 3, 20, tzinfo=UTC),
                           datetime(2026, 8, 3, 20, 30, tzinfo=UTC))],
    instruments=[InstrumentObservation("ERIS-YIT", Decimal("99.25"), "ERIS",
                                       datetime(2026, 8, 3, 20, tzinfo=UTC),
                                       datetime(2026, 8, 3, 20, 5, tzinfo=UTC))],
    contracts=[ContractMetadata("ERIS-YIT", "2Y", Decimal("42.5"), -1)],
    paper_positions=[PaperPosition("ERIS-YIT", -2)],
    working_orders=[WorkingOrder("order-1", "ERIS-YIT", OrderSide.BUY, 1)],
)
self.assertIsInstance(snapshot.rates, tuple)
self.assertEqual(snapshot.contracts[0].dv01_usd_per_bp, Decimal("42.5"))
with self.assertRaises(FrozenInstanceError):
    snapshot.decision_time_utc = datetime.now(UTC)
```

Add table-driven rejection tests for naive/non-UTC datetimes, nonfinite
Decimals, blank identifiers/source/maturity/unit, nonpositive price or DV01,
rate-sensitivity signs outside `{-1, 1}`, non-integer quantities, negative
working-order quantity, raw string order sides, and a nested item of the wrong
record type. Assert `PositionState(-1/0/1)` and `TradeDirection(-1/0/1)` map to
the documented members and other values raise `ValueError`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest docs.tests.test_strategy_models -v
```

Expected: import failure because `strategy` does not exist.

- [ ] **Step 3: Implement the exact domains and market records**

Use these exact public declarations in `strategy/models.py`:

```python
class PositionState(IntEnum):
    REVERSE = -1
    FLAT = 0
    TRADITIONAL = 1

class TradeDirection(IntEnum):
    REVERSE = -1
    FLAT = 0
    TRADITIONAL = 1

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(str, Enum):
    MARKET = "MKT"
    LIMIT = "LMT"

class TimeInForce(str, Enum):
    DAY = "DAY"

class FlattenUrgency(str, Enum):
    NONE = "none"
    SCHEDULED = "scheduled"
    EMERGENCY = "emergency"
```

Declare frozen, slotted dataclasses with these fields in this order:

```python
NamedValue(name: str, value: Decimal, unit: str)
RateObservation(series_id: str, maturity: str, rate_bps: Decimal, source: str,
                observed_at_utc: datetime, available_at_utc: datetime)
InstrumentObservation(instrument_id: str, price_points: Decimal, source: str,
                      observed_at_utc: datetime, available_at_utc: datetime)
ContractMetadata(instrument_id: str, maturity: str, dv01_usd_per_bp: Decimal,
                 rate_sensitivity_sign: int)
PaperPosition(instrument_id: str, quantity_contracts: int)
WorkingOrder(order_ref: str, instrument_id: str, side: OrderSide,
             quantity_contracts: int)
MarketSnapshot(decision_time_utc: datetime,
               rates: tuple[RateObservation, ...],
               instruments: tuple[InstrumentObservation, ...],
               contracts: tuple[ContractMetadata, ...],
               paper_positions: tuple[PaperPosition, ...] = (),
               working_orders: tuple[WorkingOrder, ...] = ())
```

Private helpers must reject rather than coerce scalar types:

```python
def _text(name: str, value: object) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be nonblank text")

def _utc(name: str, value: object) -> None:
    if type(value) is not datetime or value.utcoffset() != timedelta():
        raise ValueError(f"{name} must be a timezone-aware UTC datetime")

def _decimal(name: str, value: object, *, positive: bool = False,
             nonnegative: bool = False) -> None:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be nonnegative")
```

Each `__post_init__` calls the relevant helpers. Observation availability
cannot precede observation time. `MarketSnapshot.__post_init__` uses
`object.__setattr__(self, name, tuple(value))` for all five collection fields,
then verifies each member has the declared concrete type and each observation
is available no later than `decision_time_utc`. Export the public names from
`strategy/__init__.py`; do not export private helpers.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: all market-model tests pass with no warnings.

- [ ] **Step 5: Run the existing general suite and commit**

```powershell
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s docs/tests -v
git diff --check
git add strategy docs/tests/test_strategy_models.py
git commit -m "feat: add immutable strategy market models"
```

Expected: suite and diff check exit zero.

---

### Task 2: Immutable strategy output records

**Files:**
- Modify: `strategy/models.py`
- Modify: `strategy/__init__.py`
- Modify: `docs/tests/test_strategy_models.py`

**Interfaces:**
- Consumes: Task 1 enums, `NamedValue`, UTC datetimes, Decimals, and immutable tuples.
- Produces: `SpreadObservation`, `SignalDecision`, `TargetPosition`, `RiskDecision`, and `OrderIntent` with the exact signatures below.

- [ ] **Step 1: Write failing output-record tests**

Add one literal successful construction test per record and table-driven
validation tests. The success instances use these fields exactly:

```python
SpreadObservation(
    maturity="2Y", observation_time_utc=NOW,
    fixed_swap_spread_bps=Decimal("12.5"),
    expected_funding_spread_bps=Decimal("3.0"),
    gross_excess_spread_bps=Decimal("9.5"),
    traditional_cost_buffer_bps=Decimal("1.2"),
    reverse_cost_buffer_bps=Decimal("1.4"),
    traditional_net_opportunity_bps=Decimal("8.3"),
    reverse_net_opportunity_bps=Decimal("-10.9"),
    z_score=Decimal("1.75"), observation_count=60,
    source_quality_ok=True, is_fresh=True,
)
SignalDecision(
    decision_id="decision-1", maturity="2Y", decision_time_utc=NOW,
    prior_state=PositionState.FLAT, new_state=PositionState.TRADITIONAL,
    direction=TradeDirection.TRADITIONAL, reason_code="entry_threshold",
    feature_values=(NamedValue("z_score", Decimal("1.75"), "standard_deviations"),),
    strategy_version="swap-arb-1", configuration_version="config-1",
)
TargetPosition(
    maturity="2Y", swap_instrument_id="ERIS-YIT",
    treasury_instrument_id="CME-ZT", swap_quantity_contracts=-3,
    treasury_quantity_contracts=7, target_dv01_usd_per_bp=Decimal("1000"),
    gross_dv01_usd_per_bp=Decimal("1990"),
    residual_net_dv01_usd_per_bp=Decimal("-10"),
    expected_turnover_contracts=10, expected_cost_usd=Decimal("25.5"),
    rounding_diagnostic="minimum_residual", cap_diagnostic="within_caps",
)
RiskDecision(
    allowed=True, scale=Decimal("0.75"), reason_codes=("within_limits",),
    flatten_requested=False, urgency=FlattenUrgency.NONE,
    limits=(NamedValue("gross_dv01", Decimal("5000"), "usd_per_bp"),),
    measured_values=(NamedValue("gross_dv01", Decimal("1990"), "usd_per_bp"),),
)
OrderIntent(
    run_id="run-1", agent_id="agent-0", strategy_id="swap-arb",
    decision_id="decision-1", instrument_id="ERIS-YIT", side=OrderSide.SELL,
    quantity_contracts=3, order_type=OrderType.MARKET,
    time_in_force=TimeInForce.DAY, earliest_submission_utc=NOW,
    activate_at_utc=NOW + timedelta(minutes=1),
    expires_at_utc=NOW + timedelta(hours=1),
    reference_price_points=Decimal("99.25"),
    max_slippage_price_points=Decimal("0.05"), paper_only=True,
)
```

Assert frozen assignment fails. Assert failures for raw enum values, blank
text, naive/non-UTC timestamps, nonfinite Decimals, negative costs/counts/
slippage/DV01 targets, risk scale below zero or above one, duplicate/blank
reason codes, wrong tuple member types, timestamp ordering violations, and any
`paper_only` value other than `True`. `z_score=None` is the only nullable
numeric field.

- [ ] **Step 2: Run focused output tests and verify RED**

Run the Task 1 focused command. Expected: imports or attributes for the five
new records fail before implementation.

- [ ] **Step 3: Implement the output records minimally**

Add frozen, slotted dataclasses with precisely the fields and ordering shown in
Step 1. Enforce `type(value) is bool` for flags and `type(value) is int` for
counts/quantities so booleans cannot pass as integers. Require concrete enum
instances with `type(value) is ExpectedEnum`. Normalize `feature_values`,
`reason_codes`, `limits`, and `measured_values` to tuples before validating
members. Reject duplicate reason codes while retaining caller order.

For `OrderIntent`, validate:

```python
if not (
    self.earliest_submission_utc
    <= self.activate_at_utc
    <= self.expires_at_utc
):
    raise ValueError("intent timestamps must be ordered")
if self.paper_only is not True:
    raise ValueError("paper_only must be True")
```

Export the five records from `strategy/__init__.py`. Add no base class,
protocol, builder, registry, schema reflection, or adapter.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the focused command. Expected: all model tests pass with no warnings.

- [ ] **Step 5: Run both suites and commit**

```powershell
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s docs/tests -v
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s agents/agent_0/tests -v
git diff --check
git add strategy docs/tests/test_strategy_models.py
git commit -m "feat: add immutable strategy decision models"
```

Expected: both suites and diff check exit zero.

---

### Task 3: Stable serialization and P30 interface evidence

**Files:**
- Modify: `strategy/models.py`
- Modify: `strategy/__init__.py`
- Modify: `docs/tests/test_strategy_models.py`
- Create: `docs/verification/P30.md`

**Interfaces:**
- Consumes: any dataclass instance from Tasks 1-2.
- Produces: `to_csv_row(record: object) -> dict[str, str]` and exact interface/dependency examples in P30 evidence.

- [ ] **Step 1: Write failing literal serialization tests**

Assert `to_csv_row` rejects non-dataclass values and serializes a
`SignalDecision` into this literal row, preserving declaration order:

```python
self.assertEqual(to_csv_row(decision), {
    "decision_id": "decision-1",
    "maturity": "2Y",
    "decision_time_utc": "2026-08-03T21:00:00Z",
    "prior_state": "0",
    "new_state": "1",
    "direction": "1",
    "reason_code": "entry_threshold",
    "feature_values": '[{"name":"z_score","value":"1.75",'
                      '"unit":"standard_deviations"}]',
    "strategy_version": "swap-arb-1",
    "configuration_version": "config-1",
})
```

Add literal assertions for lowercase booleans, empty `None`, fixed-point
Decimals such as `Decimal("1E+3") -> "1000"`, enum values, compact nested JSON,
fresh dictionaries on repeat calls, and unchanged record values.

- [ ] **Step 2: Run serialization tests and verify RED**

Run the focused model command. Expected: import or attribute failure because
`to_csv_row` does not exist.

- [ ] **Step 3: Implement deterministic serialization**

Use `dataclasses.fields` and `is_dataclass`. Scalar conversion order must check
`Enum` before `int`, and `bool` before integer handling:

```python
def _serialized(value: object) -> object:
    if isinstance(value, Enum):
        return str(value.value)
    if type(value) is bool:
        return "true" if value else "false"
    if value is None:
        return ""
    if type(value) is datetime:
        _utc("datetime", value)
        return value.isoformat().replace("+00:00", "Z")
    if type(value) is Decimal:
        _decimal("decimal", value)
        return format(value, "f")
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _serialized(getattr(value, field.name))
                for field in fields(value)}
    if type(value) is tuple:
        return [_serialized(item) for item in value]
    return str(value)

def to_csv_row(record: object) -> dict[str, str]:
    if not is_dataclass(record) or isinstance(record, type):
        raise TypeError("record must be a dataclass instance")
    row: dict[str, str] = {}
    for field in fields(record):
        value = _serialized(getattr(record, field.name))
        row[field.name] = (
            json.dumps(value, separators=(",", ":"))
            if isinstance(value, (dict, list))
            else str(value)
        )
    return row
```

Export only `to_csv_row`, not `_serialized`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the focused command. Expected: all model tests pass with exact literal
serialization.

- [ ] **Step 5: Write P30 verification evidence**

Create `docs/verification/P30.md` containing:

- the six exact public record signatures and supporting value-record fields;
- enum values and validation rules;
- the literal `SignalDecision` and `OrderIntent` construction examples;
- the `to_csv_row` output example;
- dependency statement: Python standard library only, no project/file/clock/
  broker/network imports;
- MG4 prerequisite note from the design (local facts validated, historical
  P24/MG4 sign-off document absent, user authorized step approval);
- exact focused/full/compile/diff commands and their observed results;
- interface-review and simplicity-review findings and resolutions.

- [ ] **Step 6: Run final verification and commit P30**

```powershell
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest docs.tests.test_strategy_models -v
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s docs/tests -v
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s agents/agent_0/tests -v
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m compileall -q strategy docs/tests agents/agent_0
git diff --check
git status --short
git add strategy docs/tests/test_strategy_models.py
git add -f docs/verification/P30.md
git commit -m "feat: complete P30 strategy interfaces"
```

Expected: every command exits zero, test output is warning-free, and status
contains only the intended P30 files before staging.
