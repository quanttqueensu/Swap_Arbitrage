# P10 Strategy Equations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a source-verified, causal, unit-explicit P10 research contract and independently recalculable synthetic golden examples for the approved 2Y/5Y swap-arbitrage hypothesis, then stop for manual MG2 approval.

**Architecture:** P10 remains documentation-and-test only: the normative equations live in one research contract, exact synthetic inputs and expected outputs live in one JSON fixture, and a standard-library `unittest` module independently recomputes every value with `Decimal`. The economic hypothesis, executable futures basket, and exact/proxy/unavailable boundary remain separate; no production strategy behavior is created or changed.

**Tech Stack:** Markdown, JSON, Python 3.12 standard library (`unittest`, `decimal`, `datetime`, `json`, `pathlib`, `statistics` only where it does not introduce binary floating point), PowerShell, Git.

## Global Constraints

- Work only in the existing isolated worktree `C:\Users\jaydo_0v7vk2o\Downloads\Swap_Arbitrage\docs\.worktrees\p01` on branch `codex/p10-equations`.
- Preserve the unrelated dirty main checkout; stage and commit only paths named in this plan.
- P10 creates no production strategy module and changes no current proxy, broker, risk, signal, or backtest behavior.
- Do not access Quantt, Cloudflare/R2, IBKR, or public market-data APIs. Official contract/methodology pages may be read only to verify conventions and citations.
- Submit and cancel zero broker orders. The repository path `agents/agent_0/orders` must remain absent after every automated check.
- The executable maturity universe is exactly 2Y and 5Y. Keep 10Y and 30Y executable mappings unavailable.
- Use rates and spreads in basis points, P&L and costs in USD, DV01 in USD per basis point, signed integer contract quantities, UTC timestamps, and ISO observation dates.
- Exact outputs require exact maturity-matched CMS, CMT, floating-rate, collateral-consistent repo, contract, price, multiplier, and current/CTD DV01 inputs. `EFFR-SOFR` produces only a labelled proxy output.
- Funding baseline: 60 completed business days, minimum 40 consecutive lagged observations, one-business-day lag, uniform weights, and a flat 20-business-day forecast horizon.
- Z-score baseline: previous 252 completed business days, current observation excluded, sample standard deviation, full 252 required, and zero variance unavailable.
- Thresholds: traditional entry `z >= 2.0`; reverse entry `z <= -2.0`; hysteretic exit `abs(z) <= 0.5`; Agent 2 daily move qualification at `>= +5.00 bps` or `<= -5.00 bps`; additional entry buffer `0 bps`; directional net opportunity must be strictly positive.
- Traditional execution is long Eris/short Treasury; reverse execution is short Eris/long Treasury. One long contract has negative signed rate DV01.
- Round swap-leg magnitude nearest with half ties away from zero; choose the adjacent Treasury floor/ceiling quantity with minimum absolute net-DV01 residual and lower gross DV01 on a tie; allow exactly 5% residual and block above 5% or when either leg is zero/unavailable.
- A reversal is an exit plus a separately charged entry. A roll closes the old contract and opens the new contract as separately charged transactions; never use a cross-contract price change as a same-contract return.
- Fixture costs are synthetic examples, not current-market observations or frozen backtest assumptions.
- MG2 remains `Not started` until the user manually approves the completed evidence. Do not start P11.

---

## File map

| Path | Action | Single responsibility |
|---|---|---|
| `docs/research/strategy-equations.md` | Create | Normative P10 equations, source ledger, timing rules, exact/proxy/unavailable matrix, and hand calculations. |
| `tests/fixtures/strategy_equation_examples.json` | Create | Data-only synthetic inputs and expected outputs; no executable decision logic. |
| `tests/test_strategy_equation_examples.py` | Create | Independent `Decimal` recalculation, boundary, causality, state, P&L, and hedge tests. |
| `docs/verification/P10.md` | Create | Reproducible commands, review findings/resolutions, external-contact counts, limitations, and pending MG2 request. |

Do not modify `docs/master-plan/VERIFICATION_GATES.md` in this plan. The MG2 ledger changes only after explicit user approval.

### Task 1: Establish the official-source and availability contract

**Files:**
- Create: `docs/research/strategy-equations.md`
- Reference: `docs/superpowers/specs/2026-07-29-p10-strategy-equations-design.md`
- Reference: `docs/master-plan/PROJECT_CONTRACTS.md`

**Interfaces:**
- Consumes: the approved P10 design and the project-wide units/sign contracts.
- Produces: fixed section names and source identifiers used by Tasks 2–5; no Python interface.

- [ ] **Step 1: Record the pre-task safety baseline**

Run:

```powershell
git status --short --branch
if (Test-Path -LiteralPath 'agents\agent_0\orders') { exit 97 }
```

Expected: branch `codex/p10-equations`, no worktree changes, exit `0`, and no order directory.

- [ ] **Step 2: Re-open each official source and capture only the convention it supports**

Use the following direct official URLs and record an implementation verification date of `2026-07-31`:

```text
https://www.cmegroup.com/markets/interest-rates/files/eris-sofr-swap-futures-faq.pdf
https://www.cmegroup.com/markets/interest-rates/files/eris-sofr-swap-futures-overview.pdf
https://www.cmegroup.com/articles/2024/trading-swap-spreads-with-futures-a-primer-for-eristreasury-swap-spreads.html
https://www.cmegroup.com/notices/electronic-trading/2024/03/20240311.html
https://www.cmegroup.com/notices/electronic-trading/2023/11/20231106.html
https://www.cmegroup.com/education/courses/introduction-to-treasuries/understand-treasuries-contract-specifications.hideSubnav.educationIframe.html.html?hideAddThisExt=y&hideFooter=y&hideHeader=y&hideRightRail=y
https://www.cmegroup.com/markets/interest-rates/us-treasury/2-year-us-treasury-note.contractSpecs.html
https://www.cmegroup.com/markets/interest-rates/us-treasury/5-year-us-treasury-note.contractSpecs.html
https://www.cmegroup.com/content/dam/cmegroup/trading/interest-rates/files/us-treasury-futures-delivery-process.pdf
https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology
https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/interest-rates-frequently-asked-questions
https://www.newyorkfed.org/markets/reference-rates
```

Record these verified facts, without copying long passages:

| Source ID | Fact to record |
|---|---|
| `CME-ERIS-FAQ` | One contract is USD 100,000 notional; long receives fixed/pays compounded SOFR; price is indexed to 100; one price point is USD 1,000; 2Y prefix is YIT and 5Y prefix is YIW. |
| `CME-SPREAD-PRIMER` | Buy spread means buy Eris and sell Treasury; ETU 2Y displayed ratio is 2:1; EWV 5Y displayed ratio is 1:1. |
| `CME-ETU-NOTICE` | ETU is YIT versus ZT with leg quantity ratio 2:1. |
| `CME-EWV-NOTICE` | EWV is YIW versus ZF with leg quantity ratio 1:1. |
| `CME-TREASURY-SPECS` | ZT face amount/contract factor is USD 200,000/USD 2,000 per point; ZF is USD 100,000/USD 1,000 per point; long prices fall when yields rise; delivery and CTD conventions govern current DV01. |
| `UST-CMT` | CMT is a Treasury par-yield-curve estimate, not a futures yield or futures price. |
| `NYFED-RATES` | SOFR, EFFR, and repo-family rates are distinct published reference rates; `EFFR-SOFR` remains a proxy for exact `L-repo`. |

Explicitly state that the CME page showing `EAT` at 1:1 describes the YIA/YIT Eris-vs-Eris curve spread, not the `ETU` YIT/ZT Treasury spread. It therefore does not override or conflict with the ETU 2:1 notice. Published ratios remain sanity checks; current contract/CTD DV01 remains authoritative for sizing.

- [ ] **Step 3: Create the complete source-and-scope foundation**

Create `docs/research/strategy-equations.md` with these exact top-level sections in this order:

```markdown
# P10 Strategy Equations

**Status:** Proposed research contract pending MG2
**Executable universe:** 2Y and 5Y only
**Source verification date:** 2026-07-31

## Scope and non-goals
## Notation, units, and classification
## Source ledger and convention evidence
## Economic hypothesis
## Causal funding expectation
## Decision clock and movement trigger
## Causal z-score and state rules
## Directional costs and eligibility
## Executable futures direction
## Integer DV01 hedge
## Contract P&L, reversal, roll, and flattening
## Golden calculations
## Availability and proxy matrix
## Fail-closed conditions
## Deliberately unavailable items
## MG2 manual recalculation checklist
```

Fill the first three sections completely in this task. Define these classifications verbatim:

```text
exact: directly satisfies the maturity, collateral, unit, and timestamp contract
proxy: a named substitute that cannot be relabelled as exact or complete strategy output
assumed: a declared synthetic or scenario input, never presented as observed
derived: calculated only from classified inputs using a displayed equation
unavailable: absent or unvalidated; blocks the affected exact result or executable basket
```

The scope section must state that no production strategy behavior changes in P10, 10Y/30Y mappings are unavailable, and all numerical market values and costs in examples are synthetic.

- [ ] **Step 4: Check the source foundation mechanically**

Run:

```powershell
$path = 'docs\research\strategy-equations.md'
$text = Get-Content -Raw -LiteralPath $path
$required = @(
  '# P10 Strategy Equations',
  '**Status:** Proposed research contract pending MG2',
  '**Executable universe:** 2Y and 5Y only',
  '**Source verification date:** 2026-07-31',
  'CME-ERIS-FAQ', 'CME-SPREAD-PRIMER', 'CME-ETU-NOTICE',
  'CME-EWV-NOTICE', 'CME-TREASURY-SPECS', 'UST-CMT', 'NYFED-RATES',
  'ETU', '2:1', 'EWV', '1:1', 'current contract/CTD DV01'
)
$missing = @($required | Where-Object { -not $text.Contains($_) })
"MISSING_REQUIRED=$($missing.Count)"
if ($missing.Count) { $missing; exit 1 }
```

Expected: `MISSING_REQUIRED=0`.

- [ ] **Step 5: Commit the source contract foundation**

```powershell
git add -f docs/research/strategy-equations.md
git diff --cached --check
git diff --cached --name-only
git commit -m "docs: establish P10 source contract"
```

Expected staged path before commit: only `docs/research/strategy-equations.md`.

### Task 2: Add independently recalculated economic golden examples

**Files:**
- Create: `tests/test_strategy_equation_examples.py`
- Create: `tests/fixtures/strategy_equation_examples.json`
- Modify: `docs/research/strategy-equations.md`

**Interfaces:**
- Consumes: source IDs and classification vocabulary from Task 1.
- Produces test-only helpers with exact signatures:
  - `D(value: object) -> Decimal`
  - `expand_segments(segments: list[dict[str, object]]) -> list[Decimal]`
  - `mean(values: list[Decimal]) -> Decimal`
  - `sample_std(values: list[Decimal]) -> Decimal`
  - `funding_expectation(history: list[Decimal]) -> Decimal | None`
  - `causal_zscore(current: Decimal, prior: list[Decimal]) -> Decimal | None`
  - `economic_result(example: dict[str, object], fixture: dict[str, object]) -> dict[str, Decimal]`
- The helpers remain inside the test module and are not imported by production code.

- [ ] **Step 1: Write the fixture-loading test before the fixture exists**

Create `tests/test_strategy_equation_examples.py` with these imports, constants, and first test:

```python
from __future__ import annotations

import json
import unittest
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP, getcontext, localcontext
from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "strategy_equation_examples.json"
getcontext().prec = 50


def D(value: object) -> Decimal:
    return Decimal(str(value))


class FixtureContractTests(unittest.TestCase):
    def test_fixture_has_four_directional_examples(self) -> None:
        self.assertTrue(FIXTURE_PATH.exists(), f"missing fixture: {FIXTURE_PATH}")
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        examples = fixture["economic_examples"]
        self.assertEqual(len(examples), 4)
        self.assertEqual(
            [(item["maturity"], item["direction"]) for item in examples],
            [("2Y", 1), ("5Y", 1), ("2Y", -1), ("5Y", -1)],
        )
```

- [ ] **Step 2: Run the focused test and preserve the expected RED evidence**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_strategy_equation_examples.py' -v
```

Expected: exit `1` with an assertion failure containing `missing fixture:`, proving the fixture contract is absent without treating the RED state as a setup error; no import or production-module failure.

- [ ] **Step 3: Add exact Decimal calculation helpers**

Add these implementations above the test class:

```python
def expand_segments(segments: list[dict[str, object]]) -> list[Decimal]:
    values: list[Decimal] = []
    for segment in segments:
        values.extend([D(segment["value_bps"])] * int(segment["count"]))
    return values


def mean(values: list[Decimal]) -> Decimal:
    return sum(values, D("0")) / D(len(values))


def sample_std(values: list[Decimal]) -> Decimal:
    center = mean(values)
    variance = sum((value - center) ** 2 for value in values) / D(len(values) - 1)
    with localcontext() as context:
        context.prec = 50
        return variance.sqrt()


def funding_expectation(history: list[Decimal]) -> Decimal | None:
    if len(history) < 40:
        return None
    trailing = history[-60:]
    one_step = mean(trailing)
    return mean([one_step] * 20)


def causal_zscore(current: Decimal, prior: list[Decimal]) -> Decimal | None:
    if len(prior) != 252:
        return None
    sigma = sample_std(prior)
    if sigma == 0:
        return None
    return (current - mean(prior)) / sigma


def economic_result(
    example: dict[str, object], fixture: dict[str, object]
) -> dict[str, Decimal]:
    funding = funding_expectation(
        expand_segments(fixture["funding_profiles"][example["funding_profile"]])
    )
    if funding is None:
        raise AssertionError("golden example must have a valid funding history")
    gross = D(example["cms_bps"]) - D(example["cmt_bps"]) - funding
    costs = example["round_trip_costs_usd"]
    cost_usd = sum((D(value) for value in costs.values()), D("0"))
    cost_bps = cost_usd / abs(D(example["target_swap_leg_dv01_usd_per_bp"]))
    net = D(example["direction"]) * gross - cost_bps
    prior = expand_segments(
        fixture["gross_history_profiles"][example["gross_history_profile"]]
    )
    zscore = causal_zscore(gross, prior)
    if zscore is None:
        raise AssertionError("golden example must have a valid z-score history")
    return {
        "swap_spread_bps": D(example["cms_bps"]) - D(example["cmt_bps"]),
        "funding_expectation_bps": funding,
        "gross_opportunity_bps": gross,
        "round_trip_cost_usd": cost_usd,
        "round_trip_cost_bps": cost_bps,
        "net_directional_opportunity_bps": net,
        "zscore": zscore,
    }
```

- [ ] **Step 4: Create the complete economic fixture section**

Create `tests/fixtures/strategy_equation_examples.json`. Use string decimals for every non-integer numeric input and expected numeric result. Define these profiles exactly:

```json
{
  "schema_version": "p10.strategy-equations.v1",
  "classification": "assumed_synthetic",
  "funding_profiles": {
    "flat_5_bps_60": [{"value_bps": "5", "count": 60}],
    "flat_5_bps_40": [{"value_bps": "5", "count": 40}],
    "flat_5_bps_39": [{"value_bps": "5", "count": 39}]
  },
  "gross_history_profiles": {
    "mean_15_sd_5_252": [
      {"value_bps": "10", "count": 123},
      {"value_bps": "20", "count": 123},
      {"value_bps": "7.5", "count": 1},
      {"value_bps": "22.5", "count": 1},
      {"value_bps": "12.5", "count": 1},
      {"value_bps": "17.5", "count": 1},
      {"value_bps": "15", "count": 2}
    ],
    "mean_0_sd_5_252": [
      {"value_bps": "-5", "count": 123},
      {"value_bps": "5", "count": 123},
      {"value_bps": "-7.5", "count": 1},
      {"value_bps": "7.5", "count": 1},
      {"value_bps": "-2.5", "count": 1},
      {"value_bps": "2.5", "count": 1},
      {"value_bps": "0", "count": 2}
    ],
    "mean_minus_25_sd_10_252": [
      {"value_bps": "-35", "count": 123},
      {"value_bps": "-15", "count": 123},
      {"value_bps": "-40", "count": 1},
      {"value_bps": "-10", "count": 1},
      {"value_bps": "-30", "count": 1},
      {"value_bps": "-20", "count": 1},
      {"value_bps": "-25", "count": 2}
    ],
    "mean_minus_5_sd_5_252": [
      {"value_bps": "-10", "count": 123},
      {"value_bps": "0", "count": 123},
      {"value_bps": "-12.5", "count": 1},
      {"value_bps": "2.5", "count": 1},
      {"value_bps": "-7.5", "count": 1},
      {"value_bps": "-2.5", "count": 1},
      {"value_bps": "-5", "count": 2}
    ]
  },
  "economic_examples": [
    {
      "id": "traditional_2y",
      "maturity": "2Y",
      "direction": 1,
      "cms_bps": "450",
      "cmt_bps": "420",
      "funding_profile": "flat_5_bps_60",
      "gross_history_profile": "mean_15_sd_5_252",
      "target_swap_leg_dv01_usd_per_bp": "1000",
      "round_trip_costs_usd": {"swap_bid_ask": "250", "treasury_bid_ask": "250", "commission_exchange": "100", "slippage": "200", "roll": "100", "financing_not_in_funding": "100"},
      "expected": {"swap_spread_bps": "30", "funding_expectation_bps": "5", "gross_opportunity_bps": "25", "round_trip_cost_usd": "1000", "round_trip_cost_bps": "1", "net_directional_opportunity_bps": "24", "zscore": "2"}
    },
    {
      "id": "traditional_5y",
      "maturity": "5Y",
      "direction": 1,
      "cms_bps": "430",
      "cmt_bps": "410",
      "funding_profile": "flat_5_bps_60",
      "gross_history_profile": "mean_0_sd_5_252",
      "target_swap_leg_dv01_usd_per_bp": "1000",
      "round_trip_costs_usd": {"swap_bid_ask": "600", "treasury_bid_ask": "600", "commission_exchange": "400", "slippage": "800", "roll": "400", "financing_not_in_funding": "200"},
      "expected": {"swap_spread_bps": "20", "funding_expectation_bps": "5", "gross_opportunity_bps": "15", "round_trip_cost_usd": "3000", "round_trip_cost_bps": "3", "net_directional_opportunity_bps": "12", "zscore": "3"}
    },
    {
      "id": "reverse_2y",
      "maturity": "2Y",
      "direction": -1,
      "cms_bps": "380",
      "cmt_bps": "420",
      "funding_profile": "flat_5_bps_60",
      "gross_history_profile": "mean_minus_25_sd_10_252",
      "target_swap_leg_dv01_usd_per_bp": "1000",
      "round_trip_costs_usd": {"swap_bid_ask": "400", "treasury_bid_ask": "400", "commission_exchange": "200", "slippage": "500", "roll": "300", "financing_not_in_funding": "200"},
      "expected": {"swap_spread_bps": "-40", "funding_expectation_bps": "5", "gross_opportunity_bps": "-45", "round_trip_cost_usd": "2000", "round_trip_cost_bps": "2", "net_directional_opportunity_bps": "43", "zscore": "-2"}
    },
    {
      "id": "reverse_5y",
      "maturity": "5Y",
      "direction": -1,
      "cms_bps": "397",
      "cmt_bps": "410",
      "funding_profile": "flat_5_bps_60",
      "gross_history_profile": "mean_minus_5_sd_5_252",
      "target_swap_leg_dv01_usd_per_bp": "1000",
      "round_trip_costs_usd": {"swap_bid_ask": "300", "treasury_bid_ask": "300", "commission_exchange": "150", "slippage": "400", "roll": "200", "financing_not_in_funding": "150"},
      "expected": {"swap_spread_bps": "-13", "funding_expectation_bps": "5", "gross_opportunity_bps": "-18", "round_trip_cost_usd": "1500", "round_trip_cost_bps": "1.5", "net_directional_opportunity_bps": "16.5", "zscore": "-2.6"}
    }
  ],
  "movement_boundaries": [],
  "state_examples": [],
  "pnl_examples": [],
  "hedge_examples": []
}
```

- [ ] **Step 5: Add economic and causality tests**

Add test cases that:

1. compare every key returned by `economic_result` with `D(example["expected"][key])`;
2. assert every expanded gross history has length 252 and its exact declared mean/sample standard deviation;
3. assert 39 funding observations return `None`, 40 and 60 return `5`, and a 61st oldest observation is discarded;
4. assert 251 and 253 prior z observations return `None`, a constant 252-value history returns `None`, and 252 valid observations calculate a score;
5. prove the current observation is excluded by modifying the current value without modifying the prior mean or standard deviation;
6. prove adding or modifying a future observation does not change a previously calculated funding forecast or z-score;
7. assert eligibility uses `net > 0`, so `0` is ineligible and `0.0001` is eligible.

Use exact assertions such as:

```python
class EconomicEquationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_all_four_golden_examples_recalculate_exactly(self) -> None:
        for example in self.fixture["economic_examples"]:
            with self.subTest(example=example["id"]):
                actual = economic_result(example, self.fixture)
                expected = {key: D(value) for key, value in example["expected"].items()}
                self.assertEqual(actual, expected)

    def test_funding_warmup_and_window(self) -> None:
        profiles = self.fixture["funding_profiles"]
        self.assertIsNone(funding_expectation(expand_segments(profiles["flat_5_bps_39"])))
        self.assertEqual(funding_expectation(expand_segments(profiles["flat_5_bps_40"])), D("5"))
        sixty = expand_segments(profiles["flat_5_bps_60"])
        self.assertEqual(funding_expectation([D("999")] + sixty), D("5"))

    def test_zscore_requires_exact_prior_window_and_nonzero_variance(self) -> None:
        history = expand_segments(self.fixture["gross_history_profiles"]["mean_0_sd_5_252"])
        self.assertIsNone(causal_zscore(D("10"), history[:-1]))
        self.assertIsNone(causal_zscore(D("10"), history + [D("0")]))
        self.assertIsNone(causal_zscore(D("10"), [D("1")] * 252))
        self.assertEqual(causal_zscore(D("10"), history), D("2"))

    def test_strictly_positive_net_is_required(self) -> None:
        self.assertFalse(D("0") > D("0"))
        self.assertTrue(D("0.0001") > D("0"))
```

For the history profiles, assert the declared values directly:

```python
declared = {
    "mean_15_sd_5_252": (D("15"), D("5")),
    "mean_0_sd_5_252": (D("0"), D("5")),
    "mean_minus_25_sd_10_252": (D("-25"), D("10")),
    "mean_minus_5_sd_5_252": (D("-5"), D("5")),
}
```

- [ ] **Step 6: Run focused tests GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_strategy_equation_examples.py' -v
```

Expected: all Task 2 tests pass and the order directory remains absent.

- [ ] **Step 7: Add the four hand calculations to the research contract**

In `docs/research/strategy-equations.md`, write the economic, funding, cost, and z-score equations from the approved design and show all intermediate values for the four fixtures. State that the 20 identical forecast steps make the horizon average equal to the trailing mean. Include the exact 252-observation construction proving each sample standard deviation; for example, the zero-mean/5-bp profile has squared deviations totaling `6275`, so `sqrt(6275 / 251) = 5`.

- [ ] **Step 8: Commit the economic examples and tests**

```powershell
git add tests/test_strategy_equation_examples.py tests/fixtures/strategy_equation_examples.json
git add -f docs/research/strategy-equations.md
git diff --cached --check
git diff --cached --name-only
git commit -m "test: add P10 economic golden equations"
```

Expected staged paths: exactly the three Task 2 files.

### Task 3: Freeze movement, state, P&L, roll, and DV01 execution behavior

**Files:**
- Modify: `tests/test_strategy_equation_examples.py`
- Modify: `tests/fixtures/strategy_equation_examples.json`
- Modify: `docs/research/strategy-equations.md`

**Interfaces:**
- Consumes: `D`, `expand_segments`, and the fixture schema from Task 2.
- Produces test-only helpers with exact signatures:
  - `movement_direction(delta_bps: Decimal) -> int`
  - `transition(position: int, zscore: Decimal, traditional_net_bps: Decimal, reverse_net_bps: Decimal, data_ready: bool, risk_flatten: bool) -> tuple[int, tuple[str, ...]]`
  - `contract_pnl(quantity: int, multiplier_usd_per_point: Decimal, start_price: Decimal, end_price: Decimal, costs_usd: Decimal) -> Decimal`
  - `round_half_away_positive(value: Decimal) -> int`
  - `select_hedge(direction: int, target_dv01: Decimal, swap_dv01: Decimal, treasury_dv01: Decimal) -> dict[str, object]`

- [ ] **Step 1: Add RED tests for exact movement and z/state boundaries**

Add tests that resolve `movement_direction` and `transition` with `globals().get`, assert each resolved value is callable with a message such as `movement_direction must be callable`, and only then exercise the fixture cases. Run the focused module before adding helpers and expect those deliberate callable-contract assertions to fail; a `NameError`, import error, or `TypeError` is not acceptable RED evidence.

The movement fixture must contain exactly:

```json
[
  {"delta_bps": "4.99", "expected_direction": 0},
  {"delta_bps": "5.00", "expected_direction": 1},
  {"delta_bps": "5.01", "expected_direction": 1},
  {"delta_bps": "-4.99", "expected_direction": 0},
  {"delta_bps": "-5.00", "expected_direction": -1},
  {"delta_bps": "-5.01", "expected_direction": -1}
]
```

The state fixture must contain these exact cases:

```json
[
  {"id": "traditional_entry_at_boundary", "position": 0, "zscore": "2.0", "traditional_net_bps": "0.0001", "reverse_net_bps": "-0.0001", "data_ready": true, "risk_flatten": false, "expected_position": 1, "expected_actions": ["enter_traditional"]},
  {"id": "traditional_persistence", "position": 1, "zscore": "1.0", "traditional_net_bps": "1", "reverse_net_bps": "-1", "data_ready": true, "risk_flatten": false, "expected_position": 1, "expected_actions": []},
  {"id": "traditional_hysteretic_exit_at_boundary", "position": 1, "zscore": "0.5", "traditional_net_bps": "1", "reverse_net_bps": "-1", "data_ready": true, "risk_flatten": false, "expected_position": 0, "expected_actions": ["exit_traditional"]},
  {"id": "reverse_entry_at_boundary", "position": 0, "zscore": "-2.0", "traditional_net_bps": "-0.0001", "reverse_net_bps": "0.0001", "data_ready": true, "risk_flatten": false, "expected_position": -1, "expected_actions": ["enter_reverse"]},
  {"id": "traditional_to_reverse_reversal", "position": 1, "zscore": "-2.0", "traditional_net_bps": "-1", "reverse_net_bps": "2", "data_ready": true, "risk_flatten": false, "expected_position": -1, "expected_actions": ["exit_traditional", "enter_reverse"]},
  {"id": "risk_flatten_overrides_entry", "position": 1, "zscore": "-3", "traditional_net_bps": "-2", "reverse_net_bps": "2", "data_ready": true, "risk_flatten": true, "expected_position": 0, "expected_actions": ["risk_flatten"]},
  {"id": "missing_data_flattens", "position": -1, "zscore": "-3", "traditional_net_bps": "-2", "reverse_net_bps": "2", "data_ready": false, "risk_flatten": false, "expected_position": 0, "expected_actions": ["data_flatten"]},
  {"id": "nonpositive_net_exits", "position": -1, "zscore": "-1", "traditional_net_bps": "1", "reverse_net_bps": "0", "data_ready": true, "risk_flatten": false, "expected_position": 0, "expected_actions": ["exit_reverse"]}
]
```

- [ ] **Step 2: Implement the minimal movement and state calculators in the test module**

```python
def movement_direction(delta_bps: Decimal) -> int:
    if delta_bps >= D("5.00"):
        return 1
    if delta_bps <= D("-5.00"):
        return -1
    return 0


def transition(
    position: int,
    zscore: Decimal,
    traditional_net_bps: Decimal,
    reverse_net_bps: Decimal,
    data_ready: bool,
    risk_flatten: bool,
) -> tuple[int, tuple[str, ...]]:
    if risk_flatten:
        return (0, ("risk_flatten",)) if position else (0, ())
    if not data_ready:
        return (0, ("data_flatten",)) if position else (0, ())
    traditional_entry = zscore >= D("2.0") and traditional_net_bps > 0
    reverse_entry = zscore <= D("-2.0") and reverse_net_bps > 0
    if position == 0:
        if traditional_entry:
            return 1, ("enter_traditional",)
        if reverse_entry:
            return -1, ("enter_reverse",)
        return 0, ()
    if position == 1 and reverse_entry:
        return -1, ("exit_traditional", "enter_reverse")
    if position == -1 and traditional_entry:
        return 1, ("exit_reverse", "enter_traditional")
    if position == 1 and (abs(zscore) <= D("0.5") or traditional_net_bps <= 0):
        return 0, ("exit_traditional",)
    if position == -1 and (abs(zscore) <= D("0.5") or reverse_net_bps <= 0):
        return 0, ("exit_reverse",)
    return position, ()
```

Add explicit tests at `z = 1.9999`, `2.0`, `2.0001`, `-1.9999`, `-2.0`, `-2.0001`, `abs(z) = 0.4999`, `0.5`, and `0.5001` so both entry and exit inclusivity are unambiguous.

- [ ] **Step 3: Add RED P&L, reversal-cost, and roll tests**

Resolve `contract_pnl` with `globals().get` and assert it is callable before exercising it. The RED run must fail that callable-contract assertion; it must not error because the helper is absent.

Add these exact fixture objects:

```json
[
  {"id": "traditional_same_contract", "legs": [{"symbol": "YIT", "quantity": 2, "multiplier_usd_per_point": "1000", "start_price": "100.1000", "end_price": "100.1125", "costs_usd": "0"}, {"symbol": "ZT", "quantity": -1, "multiplier_usd_per_point": "2000", "start_price": "102.000000", "end_price": "101.984375", "costs_usd": "6.25"}], "expected_pnl_usd": "50"},
  {"id": "reverse_same_contract", "legs": [{"symbol": "YIW", "quantity": -1, "multiplier_usd_per_point": "1000", "start_price": "99.5000", "end_price": "99.4900", "costs_usd": "0"}, {"symbol": "ZF", "quantity": 1, "multiplier_usd_per_point": "1000", "start_price": "108.000000", "end_price": "108.015625", "costs_usd": "5.625"}], "expected_pnl_usd": "20"},
  {"id": "eris_roll", "old_contract": {"symbol": "YITH27", "quantity": 2, "multiplier_usd_per_point": "1000", "start_price": "100.1000", "end_price": "100.1100", "close_cost_usd": "3"}, "new_contract": {"symbol": "YITM27", "quantity": 2, "multiplier_usd_per_point": "1000", "start_price": "99.9000", "end_price": "99.9050", "open_cost_usd": "4"}, "expected_same_contract_pnl_usd": "30", "expected_roll_cost_usd": "7", "expected_net_pnl_usd": "23", "expected_contract_turnover": 4},
  {"id": "reversal_cost", "exit_quantity": 3, "entry_quantity": 2, "exit_cost_usd": "275", "entry_cost_usd": "325", "expected_total_cost_usd": "600", "expected_contract_turnover": 5}
]
```

The roll test must calculate old P&L from `100.1000 -> 100.1100` and new P&L from `99.9000 -> 99.9050`. Add a negative assertion that the cross-contract change `99.9000 - 100.1100` is not used.

- [ ] **Step 4: Implement and verify P&L calculations**

```python
def contract_pnl(
    quantity: int,
    multiplier_usd_per_point: Decimal,
    start_price: Decimal,
    end_price: Decimal,
    costs_usd: Decimal,
) -> Decimal:
    return D(quantity) * multiplier_usd_per_point * (end_price - start_price) - costs_usd
```

Assert traditional and reverse leg signs, exact CME multipliers, separate reversal charges, roll turnover, and the USD totals above.

- [ ] **Step 5: Add RED hedge-rounding and residual-boundary tests**

Resolve `round_half_away_positive` and `select_hedge` with `globals().get` and assert both are callable before exercising them. The RED run must fail those callable-contract assertions; it must not error because either helper is absent.

Add these exact hedge fixtures for both directions where shown:

```json
[
  {"id": "traditional_exact_5_percent", "direction": 1, "target_dv01_usd_per_bp": "1000", "swap_dv01_usd_per_bp": "100", "treasury_dv01_usd_per_bp": "950", "expected_swap_quantity": 10, "expected_treasury_quantity": -1, "expected_net_dv01_usd_per_bp": "-50", "expected_residual_fraction": "0.05", "expected_allowed": true},
  {"id": "reverse_exact_5_percent", "direction": -1, "target_dv01_usd_per_bp": "1000", "swap_dv01_usd_per_bp": "100", "treasury_dv01_usd_per_bp": "950", "expected_swap_quantity": -10, "expected_treasury_quantity": 1, "expected_net_dv01_usd_per_bp": "50", "expected_residual_fraction": "0.05", "expected_allowed": true},
  {"id": "traditional_5_01_percent_block", "direction": 1, "target_dv01_usd_per_bp": "1000", "swap_dv01_usd_per_bp": "100", "treasury_dv01_usd_per_bp": "949.9", "expected_swap_quantity": 10, "expected_treasury_quantity": -1, "expected_net_dv01_usd_per_bp": "-50.1", "expected_residual_fraction": "0.0501", "expected_allowed": false},
  {"id": "reverse_4_99_percent_allow", "direction": -1, "target_dv01_usd_per_bp": "1000", "swap_dv01_usd_per_bp": "100", "treasury_dv01_usd_per_bp": "950.1", "expected_swap_quantity": -10, "expected_treasury_quantity": 1, "expected_net_dv01_usd_per_bp": "49.9", "expected_residual_fraction": "0.0499", "expected_allowed": true},
  {"id": "tie_chooses_lower_gross", "direction": 1, "target_dv01_usd_per_bp": "300", "swap_dv01_usd_per_bp": "100", "treasury_dv01_usd_per_bp": "200", "expected_swap_quantity": 3, "expected_treasury_quantity": -1, "expected_net_dv01_usd_per_bp": "-100", "expected_residual_fraction": "0.33333333333333333333333333333333333333333333333333", "expected_allowed": false},
  {"id": "half_swap_rounds_away", "direction": 1, "target_dv01_usd_per_bp": "250", "swap_dv01_usd_per_bp": "100", "treasury_dv01_usd_per_bp": "150", "expected_swap_quantity": 3, "expected_treasury_quantity": -2, "expected_net_dv01_usd_per_bp": "0", "expected_residual_fraction": "0", "expected_allowed": true},
  {"id": "zero_swap_blocks", "direction": 1, "target_dv01_usd_per_bp": "40", "swap_dv01_usd_per_bp": "100", "treasury_dv01_usd_per_bp": "50", "expected_swap_quantity": 0, "expected_treasury_quantity": 0, "expected_net_dv01_usd_per_bp": "0", "expected_residual_fraction": "0", "expected_allowed": false}
]
```

- [ ] **Step 6: Implement the independent integer hedge selector**

Use positive input DV01 magnitudes and internal signed long-contract exposures `delta_swap = -swap_dv01` and `delta_treasury = -treasury_dv01`:

```python
def round_half_away_positive(value: Decimal) -> int:
    return int(value.quantize(D("1"), rounding=ROUND_HALF_UP))


def select_hedge(
    direction: int,
    target_dv01: Decimal,
    swap_dv01: Decimal,
    treasury_dv01: Decimal,
) -> dict[str, object]:
    if direction not in (-1, 1) or target_dv01 <= 0 or swap_dv01 <= 0 or treasury_dv01 <= 0:
        return {"swap_quantity": 0, "treasury_quantity": 0, "net_dv01": D("0"), "residual_fraction": D("0"), "allowed": False}
    swap_magnitude = round_half_away_positive(target_dv01 / swap_dv01)
    if swap_magnitude == 0:
        return {"swap_quantity": 0, "treasury_quantity": 0, "net_dv01": D("0"), "residual_fraction": D("0"), "allowed": False}
    swap_quantity = direction * swap_magnitude
    delta_swap = -swap_dv01
    delta_treasury = -treasury_dv01
    continuous = -(D(swap_quantity) * delta_swap) / delta_treasury
    floor_quantity = int(continuous.to_integral_value(rounding=ROUND_FLOOR))
    candidates = (floor_quantity, floor_quantity + 1)

    def score(treasury_quantity: int) -> tuple[Decimal, Decimal, int]:
        net = D(swap_quantity) * delta_swap + D(treasury_quantity) * delta_treasury
        gross = abs(D(swap_quantity) * delta_swap) + abs(D(treasury_quantity) * delta_treasury)
        return abs(net), gross, treasury_quantity

    treasury_quantity = min(candidates, key=score)
    net_dv01 = D(swap_quantity) * delta_swap + D(treasury_quantity) * delta_treasury
    residual_fraction = abs(net_dv01) / target_dv01
    allowed = treasury_quantity != 0 and residual_fraction <= D("0.05")
    return {"swap_quantity": swap_quantity, "treasury_quantity": treasury_quantity, "net_dv01": net_dv01, "residual_fraction": residual_fraction, "allowed": allowed}
```

Add invalid-input tests for direction `0`, zero/negative target, zero/negative swap DV01, and zero/negative Treasury DV01; every case must return a blocked zero-leg result.

- [ ] **Step 7: Run the focused suite and document every execution calculation**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_strategy_equation_examples.py' -v
```

Expected: all P10 focused tests pass.

Complete the movement, state, direction, hedge, P&L, reversal, roll, and flatten sections in `docs/research/strategy-equations.md`. Hand-calculate every fixture result, show `delta_i = -D_i` for one long contract, and include this side table:

| Direction | `d` | Eris quantity sign | Swap exposure | Treasury quantity sign |
|---|---:|---:|---|---:|
| Traditional | `+1` | positive | receive fixed/pay compounded SOFR | negative |
| Reverse | `-1` | negative | pay fixed/receive compounded SOFR | positive |

- [ ] **Step 8: Commit the execution contract and tests**

```powershell
git add tests/test_strategy_equation_examples.py tests/fixtures/strategy_equation_examples.json
git add -f docs/research/strategy-equations.md
git diff --cached --check
git diff --cached --name-only
git commit -m "test: freeze P10 execution equations"
```

Expected staged paths: exactly the three Task 3 files.

### Task 4: Complete timing, classification, and fail-closed coverage

**Files:**
- Modify: `tests/test_strategy_equation_examples.py`
- Modify: `docs/research/strategy-equations.md`

**Interfaces:**
- Consumes: all Task 2/3 test helpers and source facts.
- Produces: a complete normative P10 contract and causality proof; no new production interface.

- [ ] **Step 1: Add dated funding-selection and missing-date tests**

Add `date` and `timedelta` to the test imports and add these test-only helpers:

```python
from datetime import date, timedelta


def previous_weekday(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def consecutive_lagged_funding(
    records: list[dict[str, str]], decision_date: str
) -> list[Decimal]:
    by_date = {
        date.fromisoformat(record["observation_date"]): D(record["funding_spread_bps"])
        for record in records
    }
    cursor = previous_weekday(date.fromisoformat(decision_date))
    newest_first: list[Decimal] = []
    while len(newest_first) < 60 and cursor in by_date:
        newest_first.append(by_date[cursor])
        cursor = previous_weekday(cursor)
    return list(reversed(newest_first))
```

For this synthetic fixture only, declare Monday–Friday as the supplied business-day calendar with no holidays. Start from decision date `2027-03-01`, generate the preceding 60 weekdays at `5 bps`, and test:

- all 60 are selected and forecast to `5`;
- the oldest 20 may be removed and the remaining 40 still forecast to `5`;
- removing the second-newest required weekday breaks the consecutive suffix, leaving only the newest observation and therefore returning `None` from `funding_expectation`;
- decision-date and future-date observations at `999 bps` are ignored;
- changing an observation older than the latest 60 does not alter the forecast.

The research contract must classify the real holiday/business-day calendar as unavailable pending P11 validation; P10 proves the rule using an explicit synthetic calendar rather than silently assuming a production calendar.

- [ ] **Step 2: Add explicit synchronized-publication tests**

Add a test-only helper:

```python
def synchronized_decision_utc(records: list[dict[str, str]]) -> str | None:
    required = {"cms", "cmt", "floating", "repo"}
    if len(records) != 4 or {record["field"] for record in records} != required:
        return None
    dates = {record["observation_date"] for record in records}
    if len(dates) != 1:
        return None
    if any(record["available_utc"] <= record["observation_date"] for record in records):
        return None
    return max(record["available_utc"] for record in records)
```

Use ISO UTC strings such as `2027-01-06T20:31:00Z`; lexical ordering is valid because every fixture timestamp uses the same canonical `YYYY-MM-DDTHH:MM:SSZ` format. Test:

- all four same-date records return the latest publication timestamp;
- a missing repo record returns `None`;
- one prior-date record returns `None` rather than forward filling;
- moving one availability timestamp later moves the decision later;
- an observation with availability on/before its observation-date string returns `None`;
- future records appended after a saved decision do not alter that saved decision.

- [ ] **Step 3: Add fail-closed input-classification tests**

Add this test-only classification oracle:

```python
def classify_economic_inputs(
    records: list[dict[str, object]], maturity: str, decision_utc: str
) -> str:
    required = {"cms", "cmt", "floating", "repo"}
    if len(records) != 4 or {str(record.get("field")) for record in records} != required:
        return "unavailable"
    classifications: set[str] = set()
    for record in records:
        if record.get("unit") != "bps" or record.get("maturity") != maturity:
            return "unavailable"
        if record.get("stale") is not False or str(record.get("available_utc")) > decision_utc:
            return "unavailable"
        try:
            value = D(record.get("value_bps"))
        except (InvalidOperation, ValueError):
            return "unavailable"
        if not value.is_finite():
            return "unavailable"
        classification = str(record.get("classification"))
        if classification not in {"exact", "proxy"}:
            return "unavailable"
        classifications.add(classification)
    return "proxy" if "proxy" in classifications else "exact"


def valid_positive_decimals(*values: object) -> bool:
    try:
        parsed = [D(value) for value in values]
    except (InvalidOperation, ValueError):
        return False
    return all(value.is_finite() and value > 0 for value in parsed)
```

Build one valid four-record 2Y case using unit `bps`, `stale: false`, `classification: exact`, and availability no later than `2027-01-06T20:31:00Z`. Assert it returns `exact`. In separate subtests, assert:

- changing only repo to `classification: proxy` returns `proxy`;
- removing repo or duplicating CMS returns `unavailable`;
- unit `decimal`, maturity `5Y`, `stale: true`, availability `2027-01-06T20:31:01Z`, missing `value_bps`, `value_bps: NaN`, or an unrecognized classification each returns `unavailable`;
- replacing any future record after saving the valid input/result does not mutate the saved result.

Use `valid_positive_decimals` to assert that missing, zero, negative, and non-finite multiplier, price, swap-DV01, and Treasury-DV01 values are rejected before P&L or basket calculation. Assert that an unavailable leg is represented by the blocked zero-leg result from `select_hedge`, never by a one-leg basket.

- [ ] **Step 4: Add the exact availability/proxy matrix**

Complete the matrix in `docs/research/strategy-equations.md` with at least these rows:

| Field/output | Required classification in P10 |
|---|---|
| maturity-matched CMS 2Y/5Y history | unavailable pending P11 source validation |
| Treasury CMT 2Y/5Y | exact only when official same-date rate and publication metadata pass |
| exact floating reference `L` | unavailable until approved mapping |
| maturity/collateral-consistent repo | unavailable pending P11 |
| production business-day/holiday calendar | unavailable pending P11 |
| `EFFR-SOFR` | proxy |
| funding estimator parameters | assumed/frozen research parameters |
| example execution costs | assumed synthetic |
| current Eris DV01 | exact only from validated current contract metadata |
| current Treasury futures DV01 | exact only from validated CTD and conversion factor |
| CME displayed 2:1/1:1 ratios | exact published facts, sanity checks only |
| economic example outputs | derived synthetic |
| 2Y/5Y executable basket | derived only when all contract inputs are exact |
| 10Y/30Y executable basket | unavailable |
| intraday trigger | unavailable |
| forward funding curve | unavailable pending P11 |
| complete four-maturity strategy result | unavailable |

- [ ] **Step 5: Complete fail-closed and timestamp prose**

State exactly:

- `decision_utc` is the maximum of the required same-observation-date publication timestamps;
- no guessed fixed clock, forward fill, or intraday interpolation is permitted;
- the 60/40 funding history is a consecutive suffix ending at `t-1`; a missing required business date breaks the suffix;
- the z-score uses exactly the 252 completed business dates preceding `t` and excludes `X_gross(t)`;
- non-finite, missing, stale, wrong-unit, wrong-maturity, nonpositive price/DV01, unresolved sign/multiplier, one-leg basket, or residual above 5% blocks the affected output;
- proxy input lineage propagates to every derived output;
- risk flattening overrides entry or reversal.

- [ ] **Step 6: Audit the research contract against the approved design**

Run:

```powershell
$plan = Get-Content -Raw -LiteralPath 'docs\research\strategy-equations.md'
$required = @(
  'SS_{m,t}', 'FS_t', 'X^{gross}', 'TC^d', 'X^{net,d}', 'z_{m,t}',
  '60 completed business', 'minimum 40', '20-business-day', '252',
  'sample standard deviation', '5.00', '4.99', '5.01',
  'Traditional', 'Reverse', 'half ties away from zero', '5%',
  'same-contract', 'reversal', 'risk flatten', 'EFFR-SOFR',
  '2Y', '5Y', '10Y', '30Y', 'pending MG2'
)
$missing = @($required | Where-Object { -not $plan.Contains($_) })
"MISSING_CONTRACT_TERMS=$($missing.Count)"
if ($missing.Count) { $missing; exit 1 }
```

Expected: `MISSING_CONTRACT_TERMS=0`.

- [ ] **Step 7: Run focused and full tests before commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_strategy_equation_examples.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
if (Test-Path -LiteralPath 'agents\agent_0\orders') { exit 97 }
```

Expected: focused P10 tests pass; full suite passes with at least the prior 54 tests plus every new P10 test; no order directory.

- [ ] **Step 8: Commit the complete normative contract**

```powershell
git add tests/test_strategy_equation_examples.py
git add -f docs/research/strategy-equations.md
git diff --cached --check
git diff --cached --name-only
git commit -m "docs: complete P10 equation contract"
```

Expected staged paths: exactly the two Task 4 files.

### Task 5: Perform independent reviews and prepare the MG2 evidence package

**Files:**
- Create: `docs/verification/P10.md`
- Modify only if a review finds a concrete defect: `docs/research/strategy-equations.md`, `tests/fixtures/strategy_equation_examples.json`, `tests/test_strategy_equation_examples.py`

**Interfaces:**
- Consumes: the complete Task 1–4 diff and all command output.
- Produces: an evidence record that requests, but does not grant, MG2 approval.

- [ ] **Step 1: Create the P10 verification record before final review**

Create `docs/verification/P10.md` with these exact sections:

```markdown
# P10 Verification Record

## Objective and scope
## Code and design commits
## Files changed
## Unrelated changes preserved
## Tests written before implementation
## Command evidence
## Independent golden recalculations
## Data read or changed
## External systems contacted
## Requirements review
## Quality review
## Financial-equations review
## Causality review
## Market-units review
## Findings and resolutions
## Known limitations
## MG2 manual gate request
## Paper-only assertion
```

Record zeros for IBKR connections, submissions, modifications, cancellations, Cloudflare/R2 calls, Quantt calls, and public market-data requests. Distinguish official documentation reads from market-data reads. State `Broker-order submission count during P10 development: 0`.

- [ ] **Step 2: Run the requirements and quality reviews read-only**

Use the repository review sequence. Each reviewer reads the four master-plan files, approved P10 design, complete P10 diff, fixture, test output, and existing P01/P02 constraints. Reviewers do not edit files.

Requirements review prompt:

```text
Review P10 read-only against the approved P10 design, all four master-plan files, and the full branch diff. Identify only concrete requirement gaps, scope violations, missing golden cases, proxy mislabelling, paper-only violations, or contradictions. Verify that no production strategy behavior changed and that work stops at MG2. Cite exact file/line evidence. Return PASS if no actionable finding remains.
```

Quality review prompt:

```text
Review the corrected P10 diff read-only. Check Decimal correctness, fixture/data separation, deterministic tests, invalid-input behavior, test independence, unnecessary complexity, UTF-8/Markdown integrity, and preservation of unrelated work. Cite exact evidence and the smallest safe correction for each actionable issue. Return PASS if ready for specialist review.
```

Resolve each concrete finding in the owning task file, rerun the focused suite, commit the correction separately, and record finding, correction commit, and rerun evidence in `docs/verification/P10.md`.

- [ ] **Step 3: Run three specialist reviews read-only**

Financial-equations review prompt:

```text
Independently recalculate all four economic examples and every movement, z-score, cost, P&L, reversal, roll, and integer-DV01 example without importing test helpers. Verify units, signs, denominators, sample standard deviations, exact boundary inclusivity, and strictly positive eligibility. Cite each mismatch. Return PASS only when every fixture expected value is independently reproducible.
```

Causality review prompt:

```text
Review P10 timing read-only. Trace every observation date, publication timestamp, lag, funding window, forecast horizon, z-score window, movement interval, and roll transition. Verify current and future values cannot affect historical decisions, missing dates do not forward fill, and the synchronized decision waits for all same-date inputs. Cite exact evidence. Return PASS if no look-ahead or ambiguous clock remains.
```

Market-units review prompt:

```text
Review P10 source evidence and execution examples read-only. Verify Eris long/short swap exposure, Treasury long price/rate sign, YIT/YIW and ZT/ZF mapping, USD 1,000 Eris point value, USD 2,000 ZT point value, USD 1,000 ZF point value, CTD-DV01 authority, displayed ETU/EWV ratios, contract P&L signs, and roll accounting. Distinguish Eris-vs-Eris EAT from Eris/Treasury ETU. Return PASS if no unit, sign, multiplier, or instrument-identity defect remains.
```

Resolve each finding, rerun the focused suite, commit the correction separately, and update the verification record. Do not weaken a golden expected value or tolerance to make a finding disappear.

- [ ] **Step 4: Run the final verification matrix**

Run the guarded focused and full suites:

```powershell
$sw = [System.Diagnostics.Stopwatch]::StartNew()
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_strategy_equation_examples.py' -v
$code = $LASTEXITCODE
$sw.Stop()
"EXIT_CODE=$code"
"ELAPSED_MS=$($sw.ElapsedMilliseconds)"
if (Test-Path -LiteralPath 'agents\agent_0\orders') { exit 97 }
exit $code
```

```powershell
$sw = [System.Diagnostics.Stopwatch]::StartNew()
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
$code = $LASTEXITCODE
$sw.Stop()
"EXIT_CODE=$code"
"ELAPSED_MS=$($sw.ElapsedMilliseconds)"
if (Test-Path -LiteralPath 'agents\agent_0\orders') { exit 97 }
exit $code
```

Run compilation and existing self-checks:

```powershell
$targets = @('agents', 'tests', 'backtest.py', 'config.py', 'data_io.py', 'raw_price_data.py', 'risk_data.py', 'signal_data.py')
if (Test-Path -LiteralPath 'cloudflare_r2_test.py' -PathType Leaf) { $targets += 'cloudflare_r2_test.py' }
& .\.venv\Scripts\python.exe -m compileall -q @targets
.\.venv\Scripts\python.exe raw_price_data.py --self-check
.\.venv\Scripts\python.exe signal_data.py --self-check
.\.venv\Scripts\python.exe risk_data.py --self-check
.\.venv\Scripts\python.exe backtest.py --self-check
.\.venv\Scripts\python.exe -m pip check
```

Run repository hygiene and exact scope checks:

```powershell
git diff --check
git status --short
$changed = @(git diff --name-only 49fad29a4b70824654b29671d62bb5c1d09d8467...HEAD)
$allowed = @(
  'docs/research/strategy-equations.md',
  'docs/superpowers/plans/2026-07-31-p10-strategy-equations.md',
  'docs/superpowers/specs/2026-07-29-p10-strategy-equations-design.md',
  'docs/verification/P10.md',
  'tests/fixtures/strategy_equation_examples.json',
  'tests/test_strategy_equation_examples.py'
)
$unexpected = @($changed | Where-Object { $_ -notin $allowed })
"UNEXPECTED_PATHS=$($unexpected.Count)"
if ($unexpected.Count) { $unexpected; exit 1 }
if (Test-Path -LiteralPath 'agents\agent_0\orders') { exit 97 }
```

Record exact exit codes, test counts, elapsed times, and output summaries in `docs/verification/P10.md`.

- [ ] **Step 5: Run UTF-8, placeholder, merge-marker, and secret hygiene**

Run a fail-closed scan over the six P10-owned files. It must reject invalid UTF-8, NUL bytes, merge markers, unbalanced Markdown fences, unfinished-work markers, AWS access keys, private keys, credential assignments, and paper-account-like values other than explicitly synthetic aliases. Record counts only; never print matched secret text.

Expected: zero errors and zero unexpected account-pattern matches.

- [ ] **Step 6: Commit the final verification record**

```powershell
git add -f docs/verification/P10.md
git diff --cached --check
git diff --cached --name-only
git commit -m "docs: record P10 verification evidence"
```

Expected staged path: only `docs/verification/P10.md`, unless a documented review correction was intentionally committed earlier as its own commit.

- [ ] **Step 7: Verify the clean stop at MG2**

Run:

```powershell
git status --short --branch
git log --oneline --decorate -8
Select-String -Path 'docs\master-plan\VERIFICATION_GATES.md' -Pattern '^\| MG2 \| Not started \|'
if (Test-Path -LiteralPath 'agents\agent_0\orders') { exit 97 }
```

Expected: clean `codex/p10-equations` worktree, MG2 still `Not started`, no order directory. Present the normative research contract, fixture, focused/full results, independent review outcomes, known unavailable inputs, and manual checklist to the user. Stop. Do not update the gate ledger, merge, push, or begin P11 until the user explicitly approves the completed MG2 evidence.

---

## Final self-review checklist for the implementer

- [ ] Every approved equation, unit, sign, threshold, lag, window, and boundary appears in both the research contract and at least one exact test.
- [ ] The fixture contains two traditional and two reverse economic examples plus entry, persistence, exit, reversal, roll, flatten, P&L, and hedge cases.
- [ ] The four 252-observation profiles independently produce their declared exact means and sample standard deviations.
- [ ] The 4.99/5.00/5.01 movement boundaries and 4.99%/5.00%/5.01% hedge-residual boundaries are tested in both economic directions where symmetry matters.
- [ ] Current/future perturbations cannot change prior funding, z-score, or synchronized-decision results.
- [ ] Exact, proxy, assumed, derived, and unavailable classifications propagate to outputs without relabelling.
- [ ] No production module, broker path, external data source, or gate ledger changed.
- [ ] All review findings are resolved or explicitly block the MG2 request.
- [ ] Final status is clean, all checks pass, broker-order count is zero, and work stops for manual MG2 approval.
