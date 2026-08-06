# P34/P35 Costs, Portfolio, and Maturity Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pure P34 cost and portfolio composition, verify the end-to-end strategy path, then record the truthful P35 maturity blockers.

**Architecture:** Two standard-library modules reuse P31-P33 records and functions. Cost functions return one immutable itemized result; portfolio selection follows existing rank order and skips additions that breach gross or absolute net DV01. P35 is evidence-only until its missing prerequisites are reconciled.

**Tech Stack:** Python 3.12 standard library, `decimal`, frozen dataclasses, `unittest`.

## Global Constraints

- Apply `ponytail:ponytail` at full intensity: reuse existing code, add no dependency or speculative abstraction, and keep the smallest safety-complete diff.
- Follow strict TDD for production behavior: write one focused failing test, observe the expected failure, then add minimal code.
- New strategy code is pure and must not import pandas, IBKR, broker/order code, network, file/path, or wall-clock APIs.
- Invalid or missing realistic costs block; they never become zero.
- Cross-maturity ranking cannot violate gross or absolute net portfolio DV01 limits.
- Do not build a backtest, change P30-P33 equations, add 10Y/30Y mappings, fabricate source fields, or update MG5 without explicit user approval.
- Use the bundled Python with the existing repository site-packages: set `PYTHONPATH` to the main checkout `.venv/Lib/site-packages`.

---

### Task 1: Itemized naive and observed costs

**Files:**
- Create: `strategy/costs.py`
- Create: `docs/tests/test_costs.py`

**Interfaces:**
- Consumes: P30 `NamedValue`; P31 `directional_cost_buffer_bps`.
- Produces: frozen `CostEstimate`; same-signature `naive_cost` and `observed_cost` functions returning `CostEstimate | None`.

- [x] **Step 1: Write failing tests** for the 1,000 USD / 1 bp hand example, all six component names and values, directional observed inputs, missing/invalid values, 3 USD close plus 4 USD open roll cost, preserved Decimal context, and forbidden imports.
- [x] **Step 2: Run** `python -m unittest docs.tests.test_costs -v` and confirm failure is caused only by the missing P34 API.
- [x] **Step 3: Implement** a frozen slotted result, one private validator/calculator, and two same-signature public functions. Delegate normalization to `directional_cost_buffer_bps`; do not add a protocol, factory, fallback framework, or quote model.
- [x] **Step 4: Run** `python -m unittest docs.tests.test_costs -v` and confirm all focused tests pass.
- [x] **Step 5: Commit** `strategy/costs.py` and `docs/tests/test_costs.py` as `feat: add P34 cost models`.

### Task 2: Risk-capped portfolio composition

**Files:**
- Create: `strategy/portfolio.py`
- Create: `docs/tests/test_portfolio.py`

**Interfaces:**
- Consumes: P30 `TargetPosition` and P32 maturity rank order.
- Produces: `portfolio_dv01(targets) -> tuple[Decimal, Decimal] | None` and `select_portfolio_targets(ranked_maturities, targets, max_portfolio_gross_dv01_usd_per_bp, max_portfolio_net_dv01_usd_per_bp) -> tuple[TargetPosition, ...] | None`.

- [x] **Step 1: Write failing tests** for gross/net aggregation, rank preservation, skipping a gross breach, skipping an absolute-net breach, duplicate/malformed input rejection, and tighter limits never increasing selected gross risk.
- [x] **Step 2: Run** `python -m unittest docs.tests.test_portfolio -v` and confirm failure is caused only by the missing P34 API.
- [x] **Step 3: Implement** exact-type validation and the minimal rank-first greedy loop. Reject non-finite or negative limits; preserve the caller Decimal context.
- [x] **Step 4: Run** `python -m unittest docs.tests.test_portfolio -v` and confirm all focused tests pass.
- [x] **Step 5: Commit** `strategy/portfolio.py` and `docs/tests/test_portfolio.py` as `feat: add P34 portfolio composition`.

### Task 3: Public API, pure end-to-end example, and P34 evidence

**Files:**
- Modify: `strategy/__init__.py`
- Create: `docs/tests/test_p34_strategy_flow.py`
- Modify: `docs/research/strategy-equations.md`
- Create: `docs/verification/P34.md`

**Interfaces:**
- Consumes: Tasks 1-2 plus existing `net_opportunity_bps`, `build_target_position`, `rank_opportunities`, and `evaluate_risk`.
- Produces: stable strategy-level P34 imports and one reproducible pure strategy example.

- [x] **Step 1: Write a failing end-to-end test** that builds the approved 2Y cost result, computes net opportunity, builds 2Y/5Y target positions, selects them in P32 rank order under P33 limits, calculates portfolio DV01, and obtains an allowed P33 risk decision.
- [x] **Step 2: Run** `python -m unittest docs.tests.test_p34_strategy_flow -v` and confirm failure is caused by missing public exports.
- [x] **Step 3: Export** the P34 API and add the exact cost/portfolio equations and conservative selection rule to `strategy-equations.md`.
- [x] **Step 4: Create** `docs/verification/P34.md` with versions, fixture hash, TDD red/green evidence, hand calculations, commands/counts, files/data touched, zero external contacts/orders, review findings, known limits, and MG5 stop.
- [x] **Step 5: Run** the focused P34 tests, all `docs/tests`, Agent 0 tests, `compileall`, and `git diff --check`.
- [x] **Step 6: Commit** the public API, example, equations, and evidence as `feat: complete P34 costs and portfolio`.

### Task 4: Begin P35 and record the maturity blockers

**Files:**
- Create: `docs/verification/P35.md`

**Interfaces:**
- Consumes: the active source evidence, canonical partitions, gate ledger, golden fixtures, and completed P34 record.
- Produces: a maturity-by-maturity evidence table and exact prerequisite conflicts; no runtime scope constant while no maturity meets P35.

- [x] **Step 1: Reproduce** rates, market, contract-risk, contract-reference, paper quote/liquidity, fixture, manifest, and gate evidence for 2Y, 5Y, 10Y, and 30Y using read-only commands.
- [x] **Step 2: Create** `docs/verification/P35.md` stating that all four maturities are currently unsupported, listing every blocker, documenting the P24 manifest-policy conflict, reserving but not emitting `complete_2y_5y`, and confirming no P40 work began.
- [x] **Step 3: Run** document/schema tests and `git diff --check`; verify the record contains no approval claim and the gate ledger is unchanged.
- [x] **Step 4: Commit** the blocker record as `docs: record P35 maturity blockers`.
