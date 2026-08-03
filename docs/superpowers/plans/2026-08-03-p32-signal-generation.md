# P32 Causal Signal Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement causal excess-spread z-scores, exact state transitions, immutable signal decisions, and deterministic cross-maturity opportunity ranking.

**Architecture:** Add one pure standard-library module, `strategy.signal_generation`. Direct functions consume P30 `SpreadObservation` values and explicit state, produce `SignalDecision` values, and leave the existing pandas price-residual proxy untouched.

**Tech Stack:** Python 3.12 standard library (`Decimal`, `localcontext`, `Sequence`, `datetime`) and `unittest`.

## Global Constraints

- Work directly on `main`; the user explicitly waived worktree isolation and intermediate approval gates.
- Ponytail full mode applies: one module and one focused test file; no engine, class, registry, service, builder, configuration layer, or new dependency.
- Reuse `STRATEGY_SPEC_VERSION = "p10.strategy-equations.v1"` from `strategy.spread` and the existing fixture without copying or modifying it.
- Frozen fixture: `docs/tests/fixtures/strategy_equation_examples.json`, SHA-256 `3fb9da5fc9ad255587ce93ea9770552f42566d56070f68ad1661709c030fbd76`.
- Use exact finite `Decimal` values and local precision 50 for Decimal arithmetic (mean, variance, square root, and division). Exact comparisons and `copy_abs()` are context-free and need no local-context wrapper. Never mutate caller/global Decimal context or use float tolerance.
- Causal z-score uses exactly 252 prior observations, sample standard deviation, and excludes current/future rows.
- Use exact `PositionState`, exact bools, strict-positive economic eligibility, inclusive entry/exit thresholds, ordered reversal actions, and flatten precedence.
- Production code has no file reads, implicit clock, business-calendar inference, pandas/numpy, orders, broker, network, or source integration.
- `signal_pipeline.py` remains unchanged and remains the labelled legacy price-residual proxy.
- P30 observations lack publication timestamps and the approved production calendar; P32 enforces timestamp ordering but does not claim the unavailable revision/calendar guarantees.
- P10 omitted its promised cross-maturity ranking score/tie-break. P32 explicitly uses descending absolute excess-spread z-score and ascending maturity text as a documented provisional convention.
- Review subagents request Luna high. If the runtime rejects Luna, use Terra high and record that no Sol reviewer was used.

---

### Task 1: Causal 252-observation z-score

**Files:**
- Create: `strategy/signal_generation.py`
- Modify: `strategy/__init__.py`
- Create: `docs/tests/test_signal_generation.py`

**Interfaces:**
- Consumes: `SpreadObservation` current value and a sequence of prior `SpreadObservation` values.
- Produces: `causal_zscore(current: SpreadObservation, prior: object) -> Decimal | None`.

- [ ] **Step 1: Write fixture-backed RED tests**

Create a test helper that constructs real immutable observations with explicit
UTC times and exact values:

```python
def observation(maturity, when, gross, *, z_score=None, count=252,
                quality=True, fresh=True):
    return SpreadObservation(
        maturity=maturity,
        observation_time_utc=when,
        fixed_swap_spread_bps=Decimal("0"),
        expected_funding_spread_bps=Decimal("0"),
        gross_excess_spread_bps=gross,
        traditional_cost_buffer_bps=Decimal("0"),
        reverse_cost_buffer_bps=Decimal("0"),
        traditional_net_opportunity_bps=gross,
        reverse_net_opportunity_bps=-gross,
        z_score=z_score,
        observation_count=count,
        source_quality_ok=quality,
        is_fresh=fresh,
    )
```

Load the fixture bytes, assert the exact SHA and schema, expand every
`gross_history_profiles` segment into literal Decimals, and use the four
economic examples to assert z-scores `2`, `3`, `-2`, and `-2.6`. Expected
values come from the frozen fixture, not a copied z-score formula.

- [ ] **Step 2: Add causal, warm-up, and numerical boundary tests**

Use literal cases to assert:

- 251 and 253 priors return `None`; exactly 252 works;
- 252 identical values return `None` for zero variance;
- current is excluded from moments;
- reversed time, duplicate time, a current/future prior, mismatched maturity,
  poor-quality history, non-sequence, strings, and non-observation members
  return `None`;
- constructing or perturbing a later future observation does not change the
  saved result at `t`;
- precision-2 caller context produces the exact fixture z-score and the entire
  context settings/flags/traps remain unchanged.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest docs.tests.test_signal_generation -v
```

Expected: import failure because `strategy.signal_generation` does not exist.

- [ ] **Step 4: Implement the minimal z-score**

In `strategy/signal_generation.py`, validate an exact `SpreadObservation`
current and a non-string `Sequence` of exactly 252 exact observations. Iterate
once to enforce same maturity, true source quality, strict time increase, and
every time before current. Under `localcontext()` precision 50, calculate:

```python
mean = sum(values, Decimal("0")) / Decimal(252)
variance = sum(((value - mean) ** 2 for value in values), Decimal("0")) / Decimal(251)
if variance == 0:
    return None
return (current.gross_excess_spread_bps - mean) / variance.sqrt()
```

Keep all arithmetic, including subtraction and squaring, inside the local
context. Export `causal_zscore` from `strategy/__init__.py`.

- [ ] **Step 5: Run focused/docs tests and commit**

Run the focused command, docs discovery, and `git diff --check`. Commit:

```text
feat: add P32 causal z-score
```

Expected: fixture outputs match exactly; no caller-context mutation.

---

### Task 2: State transitions and immutable signal decisions

**Files:**
- Modify: `strategy/signal_generation.py`
- Modify: `strategy/__init__.py`
- Modify: `docs/tests/test_signal_generation.py`

**Interfaces:**
- Consumes: exact state/threshold values plus Task 1 `causal_zscore`.
- Produces: `signal_transition(...) -> tuple[PositionState, tuple[str, ...]] | None` and `generate_signal_decision(...) -> SignalDecision | None` using the exact design signatures.

- [ ] **Step 1: Write state-machine RED tests from every frozen row**

For all eight `state_examples`, convert the position to `PositionState`, call
`signal_transition`, and assert the literal expected position and ordered
action tuple. Add literal entry boundaries `1.9999/2.0/2.0001` and
`-1.9999/-2.0/-2.0001`, plus traditional exit `0.4999/0.5/0.5001`.

Cover reverse persistence/exit, reverse-to-traditional reversal, risk flatten
while open and already flat, missing z-score, stale-data flatten, and strict
net `0` versus `0.0001`. Reject raw ints for state, float/NaN/Infinity
economics, and non-bool readiness/flatten flags.

- [ ] **Step 2: Verify transition tests RED**

Run the focused command. Expected: missing `signal_transition` import.

- [ ] **Step 3: Implement direct transition logic**

Validate exact types, then implement this precedence and no other behavior:

```python
if risk_flatten:
    return (PositionState.FLAT, ("risk_flatten",)) if prior_state else (PositionState.FLAT, ())
if not data_ready or z_score is None:
    return (PositionState.FLAT, ("data_flatten",)) if prior_state else (PositionState.FLAT, ())
traditional_entry = z_score >= Decimal("2.0") and traditional_net_bps > 0
reverse_entry = z_score <= Decimal("-2.0") and reverse_net_bps > 0
```

Then reproduce the frozen flat entry, ordered opposite-entry reversal, inclusive
`z.copy_abs() <= Decimal("0.5")` exit, nonpositive-net exit, and persistence
branches. These exact comparisons and `copy_abs()` are context-free; no
local-context wrapper is required, and the caller-context guarantee remains.

- [ ] **Step 4: Write integrated `SignalDecision` RED tests**

Using real Task 1 histories, assert:

- a traditional entry decision has the observation timestamp, prior/new state,
  traditional direction, `enter_traditional`, supplied versions, and exact
  `z_score`, traditional-net, and reverse-net `NamedValue` tuple;
- persistence reason codes are `remain_flat`, `hold_traditional`, or
  `hold_reverse`;
- reversal is `exit_traditional_then_enter_reverse`;
- stale/poor-quality/short/zero-variance history and a mismatched declared
  z-score produce data-unavailable/flatten reasons;
- risk flatten bypasses an empty/invalid economic history and has no features;
- returned decisions are frozen and slotted through the P30 model;
- malformed IDs, versions, states, observation, prior collection, or bools
  return `None` without reading a clock.

- [ ] **Step 5: Implement decision generation minimally**

Risk flatten first. Otherwise call `causal_zscore`; data is ready only when
current quality and freshness are true, observation count is exactly 252, the
z-score exists, and any declared non-null z-score equals it. Call
`signal_transition`.

Map the new state to `TradeDirection(new_state.value)`. Build reason codes from
the ordered actions or the explicit hold/data state described in the design.
Build normal features in this declaration order:

```python
(
    NamedValue("z_score", z_score, "standard_deviations"),
    NamedValue("traditional_net_opportunity", observation.traditional_net_opportunity_bps, "bps"),
    NamedValue("reverse_net_opportunity", observation.reverse_net_opportunity_bps, "bps"),
)
```

For data-unavailable decisions use `observation_count`, gross, both nets, and
the z-score only when calculated. Catch only model input `TypeError`/
`ValueError` and return `None`; do not hide arithmetic/programming errors.
Export both functions.

- [ ] **Step 6: Run focused, docs, and Agent0 suites; commit**

Run focused tests, docs discovery, Agent0 discovery, and `git diff --check`.
Commit:

```text
feat: add P32 signal state transitions
```

Expected: all eight frozen transitions and integrated decision cases pass.

---

### Task 3: Cross-maturity ranking and MG5 evidence

**Files:**
- Modify: `strategy/signal_generation.py`
- Modify: `strategy/__init__.py`
- Modify: `docs/tests/test_signal_generation.py`
- Create: `docs/verification/P32.md`

**Interfaces:**
- Consumes: synchronized `SpreadObservation` values.
- Produces: `rank_opportunities(observations: object) -> tuple[str, ...] | None` and the compact P32 golden decision trace.

- [ ] **Step 1: Write ranking RED tests**

Use synchronized real observations and literal expectations:

- eligible 2Y z `2.1` and 5Y z `-3` rank `("5Y", "2Y")`;
- equal absolute z ranks maturity text ascending, `("2Y", "5Y")`;
- nonpositive directional net excludes an otherwise extreme row;
- below-threshold, stale, poor-quality, missing-z, and observation-count 251
  rows are excluded;
- no eligible rows and an empty sequence return `()`;
- duplicate maturity, mismatched decision time, malformed collection, string,
  or non-observation member returns `None`;
- input order cannot change ranks and caller Decimal context is unchanged.

- [ ] **Step 2: Verify ranking tests RED**

Run focused tests. Expected: missing `rank_opportunities` import.

- [ ] **Step 3: Implement stable two-key ranking**

Validate the synchronized unique-maturity collection. Filter ready rows and
apply the same strict-positive/inclusive entry rules. To avoid context-sensitive
unary Decimal negation, use two stable standard-library sorts:

```python
ranked.sort(key=lambda item: item.maturity)
ranked.sort(key=lambda item: item.z_score.copy_abs(), reverse=True)
return tuple(item.maturity for item in ranked)
```

Export the function. Add no ranking class or configuration object.

- [ ] **Step 4: Create exact P32 evidence**

Create `docs/verification/P32.md` with:

- P31 MG5 predecessor and P32 commit range;
- strategy version, fixture path/hash, interface signatures, thresholds,
  feature ordering, reason-code mapping, and scope exclusions;
- exact z-score output for all four gross histories;
- all eight frozen state rows as prior state/input/expected/production/action
  trace;
- ranking examples and deterministic tie output;
- future perturbation, warm-up, zero variance, stale/missing, timestamp,
  sorting, and caller-context evidence;
- explicit publication-time/business-calendar limitation and the provisional
  ranking convention gap;
- proof `signal_pipeline.py` remains unchanged and labelled legacy proxy;
- Ponytail statement: one stdlib module, no dependency/engine/class;
- exact RED/GREEN, full-suite, compile, self-check, diff/status/hash outcomes;
- task-review findings/fixes, leaving final requirements/lookahead/Ponytail/
  whole-branch verdicts explicitly pending.

- [ ] **Step 5: Run task verification and commit**

Run:

```powershell
& 'C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest docs.tests.test_signal_generation -v
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

Commit:

```text
feat: complete P32 signal generation
```

After task review approval, dispatch requirements and lookahead reviewers plus
a Ponytail complexity reviewer. Record their exact verdicts, run a senior
whole-branch review, apply at most one final fix wave, then rerun every command
fresh on the exact final tree. Stop before P33.
