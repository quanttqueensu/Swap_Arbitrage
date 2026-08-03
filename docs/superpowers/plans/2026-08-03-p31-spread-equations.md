# P31 Spread Equations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the MG2-approved spread, funding, cost, DV01 hedge, and basket P&L equations as minimal pure Decimal functions.

**Architecture:** Add one standard-library-only `strategy.spread` module. Direct functions return Decimal values, `None` for unavailable scalar calculations, `(0, 0)` for a blocked hedge, and plain tuples for basket legs; production tests load the existing frozen fixture byte-for-byte.

**Tech Stack:** Python 3.12 standard library (`Decimal`, `localcontext`, `ROUND_FLOOR`, `ROUND_HALF_UP`, `re`, `unittest`).

## Global Constraints

- Work directly on `main`; the user explicitly waived worktree isolation and intermediate approval gates.
- Ponytail full mode applies: reuse the approved fixture and stdlib; no result classes, equation engine, registry, builder, service, or new dependency.
- The exact production identifier is `STRATEGY_SPEC_VERSION = "p10.strategy-equations.v1"`.
- Reuse `docs/tests/fixtures/strategy_equation_examples.json` without modifying or copying it; starting SHA-256 is `3fb9da5fc9ad255587ce93ea9770552f42566d56070f68ad1661709c030fbd76`.
- Use exact finite `Decimal` inputs and local precision 50 for division; never mutate the global Decimal context and never use float tolerances.
- Invalid/missing scalar inputs return `None`; invalid hedges and zero rounded swap legs return `(0, 0)`.
- Accept only exact non-flat `TradeDirection` for directional calculations.
- No file reads in production, clock, calendar inference, rolling z-score, signal/state behavior, orders, broker, network, or data-source integration.
- The absent P11 source matrix and unavailable production calendar remain explicit limitations; do not fabricate them.
- Review subagents request `gpt-5.6-luna` with high reasoning. If rejected by the runtime, use `gpt-5.6-terra` high and record that no Sol reviewer was used.

---

### Task 1: Units, economic spreads, funding, costs, and net opportunity

**Files:**
- Create: `strategy/spread.py`
- Modify: `strategy/__init__.py`
- Create: `docs/tests/test_spread.py`

**Interfaces:**
- Consumes: exact `Decimal`, exact integer quote components, `TradeDirection`, and the frozen quote/funding/economic fixture sections.
- Produces: `STRATEGY_SPEC_VERSION`, `rate_decimal_to_bps`, `treasury_fractional_quote_to_points`, `tick_value_usd`, `fixed_swap_spread_bps`, `funding_spread_bps`, `expected_funding_bps`, `gross_excess_spread_bps`, `directional_cost_buffer_bps`, and `net_opportunity_bps`.

- [ ] **Step 1: Write failing fixture-driven economic tests**

Create `docs/tests/test_spread.py`. Read the fixture only in tests. Assert its
SHA-256 and schema version before using it:

```python
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "strategy_equation_examples.json"
FIXTURE_SHA256 = "3fb9da5fc9ad255587ce93ea9770552f42566d56070f68ad1661709c030fbd76"

fixture_bytes = FIXTURE_PATH.read_bytes()
self.assertEqual(hashlib.sha256(fixture_bytes).hexdigest(), FIXTURE_SHA256)
self.assertEqual(json.loads(fixture_bytes)["schema_version"], STRATEGY_SPEC_VERSION)
```

For all four `economic_examples`, expand the named funding profile to exact
Decimals, call the production functions, and assert the literal fixture
results for swap spread, funding expectation, gross opportunity, round-trip
cost USD (cost buffer multiplied by target DV01), cost buffer, and directional
net opportunity. Convert fixture direction with
`TradeDirection(example["direction"])`; do not copy expected formulas into a
test helper.

For every quote convention, assert `tick_value_usd`. For Treasury source quotes
in the two same-contract P&L examples, assert exact normalized endpoint prices.

- [ ] **Step 2: Add systematic boundary tests before implementation**

Use literal tables:

```python
self.assertEqual(rate_decimal_to_bps(Decimal("0.045")), Decimal("450"))
self.assertEqual(rate_decimal_to_bps(Decimal("-0.001")), Decimal("-10"))
self.assertEqual(funding_spread_bps(Decimal("5"), Decimal("2")), Decimal("3"))
self.assertIsNone(expected_funding_bps([Decimal("5")] * 39))
self.assertEqual(expected_funding_bps([Decimal("5")] * 40), Decimal("5"))
self.assertEqual(expected_funding_bps([Decimal("999")] + [Decimal("5")] * 60), Decimal("5"))
```

Assert traditional/reverse symmetry for `net_opportunity_bps`; exact zero costs;
invalid flat/raw directions; negative costs; nonpositive cost-base DV01; bad
fractional quote fields; and `None`, float, NaN, or Infinity in each relevant
function. Capture `getcontext().copy()` before calls and assert it is unchanged.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest docs.tests.test_spread -v
```

Expected: import failure because `strategy.spread` does not exist.

- [ ] **Step 4: Implement the minimal economic functions**

Use only these private helpers:

```python
def _decimal(value: object, *, positive: bool = False,
             nonnegative: bool = False) -> Decimal | None:
    if type(value) is not Decimal or not value.is_finite():
        return None
    if positive and value <= 0:
        return None
    if nonnegative and value < 0:
        return None
    return value

def _divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return numerator / denominator
```

Implement direct subtraction/multiplication for conversion and spreads.
`expected_funding_bps` accepts only a non-string sequence of exact finite
Decimals, returns `None` below 40, uses only `history[-60:]`, and returns their
mean once. `directional_cost_buffer_bps` has the seven exact parameters in the
design, validates six nonnegative costs and positive cost-base DV01, then uses
`_divide(sum(costs, Decimal("0")), base)`. `net_opportunity_bps` requires
`type(direction) is TradeDirection` and rejects `FLAT`.

Implement Treasury quote validation with exact ints (not bools),
`whole_points >= 0`, `0 <= thirty_seconds < 32`, and
`0 <= eighths_of_32nd < 8`.

Export the Task 1 names from `strategy/__init__.py`.

- [ ] **Step 5: Run focused and general tests, then commit**

```powershell
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest docs.tests.test_spread -v
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s docs/tests -v
git diff --check
git add strategy docs/tests/test_spread.py
git commit -m "feat: add P31 spread and funding equations"
```

Expected: focused and general suites pass; fixture hash remains exact; diff check exits zero.

---

### Task 2: Integer DV01 hedge and residual calculations

**Files:**
- Modify: `strategy/spread.py`
- Modify: `strategy/__init__.py`
- Modify: `docs/tests/test_spread.py`

**Interfaces:**
- Consumes: exact non-flat `TradeDirection`, positive target/swap/Treasury DV01 magnitudes, and all seven frozen hedge examples.
- Produces: `dv01_hedge_quantities(...) -> tuple[int, int]`, `residual_dv01_usd_per_bp(...) -> Decimal | None`, and `residual_fraction(...) -> Decimal | None`.

- [ ] **Step 1: Write failing production-function hedge tests**

For every frozen `hedge_examples` row:

```python
swap_quantity, treasury_quantity = dv01_hedge_quantities(
    TradeDirection(example["direction"]),
    Decimal(example["target_dv01_usd_per_bp"]),
    Decimal(example["swap_dv01_usd_per_bp"]),
    Decimal(example["treasury_dv01_usd_per_bp"]),
)
net = residual_dv01_usd_per_bp(
    swap_quantity, treasury_quantity,
    Decimal(example["swap_dv01_usd_per_bp"]),
    Decimal(example["treasury_dv01_usd_per_bp"]),
)
fraction = residual_fraction(net, Decimal(example["target_dv01_usd_per_bp"]))
allowed = swap_quantity != 0 and treasury_quantity != 0 and fraction <= Decimal("0.05")
```

Assert every expected quantity, net DV01, residual fraction, and allowed flag
literally. The `(0, 0)` result has literal zero net/fraction and `allowed=False`.

- [ ] **Step 2: Add symmetry, rounding, and invalid-input tests**

Assert:

- `2.49 -> 2`, `2.5 -> 3`, `2.51 -> 3`, and `0.5 -> 1` through hedge cases;
- traditional/reverse quantities and residuals are exact sign mirrors;
- the tie case selects lower gross DV01, then lower integer Treasury quantity;
- direction `FLAT`, raw int direction, target/DV01 zero or negative, None,
  float, NaN, and Infinity return `(0, 0)`;
- residual calculation rejects bool/non-int quantities and nonpositive DV01;
- residual fraction rejects nonpositive target and missing/nonfinite net.

- [ ] **Step 3: Run focused tests and verify RED**

Run the Task 1 focused command. Expected: missing hedge-function imports.

- [ ] **Step 4: Implement the approved two-candidate selector**

Use exact positive validation. Calculate the positive swap magnitude with
`ROUND_HALF_UP`; apply the direction; use signed long-contract exposures
`-swap_dv01` and `-treasury_dv01`; calculate continuous Treasury quantity at
local precision 50; test only floor and floor+1. Select by:

```python
(abs(net_dv01), gross_dv01, treasury_quantity)
```

Return `(0, 0)` for invalid inputs or zero swap magnitude. Do not enforce the
5% risk boundary in the selector. Implement residual and fraction separately
with local precision 50, then export all three names.

- [ ] **Step 5: Run focused and both full suites, then commit**

```powershell
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest docs.tests.test_spread -v
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s docs/tests -v
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s agents/agent_0/tests -v
git diff --check
git add strategy docs/tests/test_spread.py
git commit -m "feat: add P31 DV01 hedge equations"
```

Expected: all seven hedge cases and full suites pass exactly.

---

### Task 3: Basket P&L, turnover, and P31 verification evidence

**Files:**
- Modify: `strategy/spread.py`
- Modify: `strategy/__init__.py`
- Modify: `docs/tests/test_spread.py`
- Create: `docs/verification/P31.md`

**Interfaces:**
- Consumes: tuple basket legs, nonnegative total costs, signed integer quantities, all four frozen P&L/turnover examples, and Task 1-2 functions.
- Produces: `basket_pnl_usd(legs, total_cost_usd) -> Decimal | None`, `contract_turnover_contracts(quantities) -> int | None`, and complete P31 verification evidence.

- [ ] **Step 1: Write failing basket P&L and turnover tests**

For `traditional_same_contract` and `reverse_same_contract`, turn each fixture
leg into:

```python
(
    leg["start_instrument_id"], leg["end_instrument_id"], leg["quantity"],
    Decimal(leg["multiplier_usd_per_point"]), Decimal(leg["start_price"]),
    Decimal(leg["end_price"]),
)
```

Pass summed fixture costs separately and assert exact expected basket P&L.

For `eris_roll`, call `basket_pnl_usd` at the roll boundary with the old leg
only and both close/open costs; assert expected net-at-roll. Then call with old
and new same-contract legs and the same total costs; assert expected net P&L.
Assert turnover from old/new quantities. Change the unused future leg's end
price and prove the roll-boundary result is unchanged by continuing to pass
only the old leg.

For `reversal_cost`, assert turnover of exit and entry quantities and assert
the exact USD 600 total through `directional_cost_buffer_bps` with a cost base
of `Decimal("1")` and the other four costs zero.

- [ ] **Step 2: Add invalid basket and property tables**

Assert `None` for an empty/malformed leg, start/end ID mismatch, root-only ID,
unapproved root, nonquarterly month, bool quantity, nonpositive multiplier or
price, non-Decimal values, nonfinite values, negative total cost, and cross-
contract endpoint price change. Assert turnover is zero for an empty tuple,
absolute for signed quantities, and `None` for bool/float members.

- [ ] **Step 3: Run focused tests and verify RED**

Run the focused command. Expected: missing basket/turnover imports.

- [ ] **Step 4: Implement basket arithmetic minimally**

Compile one private full-contract regex:

```python
r"(?:YIT|YIW|ZT|ZF)[HMUZ]\d{2}"
```

`basket_pnl_usd` requires a nonempty non-string sequence. Each leg is an exact
six-item tuple, both endpoint IDs are exact strings matching the regex and are
equal, quantity is an exact int, multiplier/prices are positive exact finite
Decimals, and total cost is nonnegative. Return:

```python
sum(Decimal(quantity) * multiplier * (end - start) for each leg) - total_cost
```

`contract_turnover_contracts` accepts a non-string sequence of exact ints and
returns `sum(abs(quantity) ...)`. Export both names.

- [ ] **Step 5: Create P31 verification evidence**

Create `docs/verification/P31.md` recording:

- `p10.strategy-equations.v1`, frozen fixture path/hash before and after, and
  the obsolete playbook path resolution;
- the MG2 approval record plus the absent P11 matrix/production-calendar
  limitation without claiming they exist;
- exact function signatures, units, signs, cost base, half-away rounding,
  two-candidate tie-break, residual boundary, and no-tolerance policy;
- a line-by-line table for all four economic, seven hedge, and four P&L/
  turnover examples, with expected versus production output;
- property/boundary coverage and Ponytail statement: direct stdlib functions,
  no new dependency/abstraction;
- exact RED/GREEN, full-suite, compile, four self-check, diff, status, fixture-
  hash, external-contact zero, broker/order zero commands and outcomes;
- prior task-review findings and fixes.

Do not invent the pending final equations, numerical, simplicity, or whole-
branch review results; the parent will supply their exact completed verdicts
for one evidence-only follow-up.

- [ ] **Step 6: Run final task verification and commit**

```powershell
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest docs.tests.test_spread -v
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s docs/tests -v
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s agents/agent_0/tests -v
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m compileall -q strategy docs/tests agents/agent_0
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m data_pipeline.historical_data.historical_data_builder --self-check
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' signal_pipeline.py --self-check
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' risk_pipeline.py --self-check
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' backtest_engine.py --self-check
git diff --check
git status --short
```

Expected: all commands exit zero; the known third-party `eventkit` warning may
remain; no fixture byte changes, network/broker actions, or order directory.

```powershell
git add strategy docs/tests/test_spread.py
git add -f docs/verification/P31.md
git commit -m "feat: complete P31 spread equations"
```
