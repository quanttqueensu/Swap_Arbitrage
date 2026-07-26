# Swap Arbitrage Master Plan

> **For agentic workers:** Read all four files in `docs/master-plan/` before
> changing code or data. Execute only one numbered prompt from
> `PROMPT_PLAYBOOK.md` at a time. Use tests first, preserve unrelated work, and
> stop at every stated manual gate.

**Goal:** Determine whether the swap-arbitrage hypothesis contains repeatable
paper-trading alpha while building one causal, testable strategy core that can
serve both full-strategy backtests and incremental IBKR paper agents.

**Architecture:** Pure strategy functions consume canonical market snapshots
and produce signals, target positions, risk decisions, and order intents.
Backtest and IBKR-paper adapters supply data and simulate or execute those
intents without putting historical-data logic or broker calls inside the
strategy core. Incremental agents add exactly one approved behavior at a time;
only the complete and naive-complete strategies receive portfolio backtests.

**Primary documents:**

- `MASTER_PLAN.md`: outcomes, sequencing, current-state audit, and phase gates.
- `PROJECT_CONTRACTS.md`: equations, interfaces, data schemas, and permanent
  invariants.
- `PROMPT_PLAYBOOK.md`: small, copy-paste prompts executed in order.
- `VERIFICATION_GATES.md`: automated evidence, manual sign-offs, sub-agent
  reviews, and agent promotion rules.

## Permanent project constraints

1. The project is paper-only forever. No phase may add a production account,
   production port, live-capital toggle, real-money instructions, or a path to
   enable them.
2. In this project, “live” means current market data and orders sent only to an
   IBKR paper account.
3. Agents and backtests answer different questions:
   - Agents are incremental paper experiments and operational tests.
   - Backtests evaluate only the complete strategy and a naive complete
     strategy with fixed cost and spread assumptions.
   - Do not create historical portfolio backtests for individual agents.
   - Unit tests and deterministic snapshot replays of agent decisions are
     required verification; they are not agent performance backtests.
4. Agent 0 is fully random. Each later agent inherits its predecessor and adds
   exactly one approved, measurable behavior.
5. Signal generation, position sizing, risk signals, costs, and portfolio
   decisions must be broker-independent pure code shared by backtest and paper
   execution.
6. All research features must be causal. A decision can use only information
   observable by its decision timestamp.
7. Quantt/Cloudflare is the preferred historical source. IBKR is the paper
   execution and later paper-market-data source. Source fallbacks must be
   labelled and must never silently replace a requested field.
8. Research-ready and paper-run datasets are narrow CSV files with explicit
   schemas, unique keys, deterministic ordering, units, source identifiers,
   and no duplicate or unused columns.
9. A proxy signal must be labelled as a proxy. Results from a price residual
   cannot be described as a test of the economic excess-spread hypothesis.
10. No phase promotes a result because P&L is positive by itself. Promotion
    requires data quality, operational integrity, risk compliance, and
    incremental evidence under the gates in `VERIFICATION_GATES.md`.

## Current-state audit

This table records the starting point observed on 2026-07-26. It is a baseline,
not a criticism and not evidence that the strategy succeeds or fails.

| Area | Observed state | Consequence |
|---|---|---|
| Runtime | `.venv` points to a missing Windows Store Python; no dependency manifest exists | No test result is currently reproducible from a clean machine |
| Tests | `tests/test_dv01_pipeline.py` contains 28 research-pipeline tests | The data/risk/backtest path has meaningful tests, but the only agent has none |
| Agent 0 | Paper-only IBKR implementation exists under `agents/agent_0/` | Preserve its safety controls while establishing a tested baseline |
| Agent 0 settings | Code and `SETTINGS.md` use 5 orders/day (50/week); an older design says 20/day (100/week); an older plan says 50/day (250/week) | Resolve the authoritative setting explicitly; do not infer it from old prose |
| Hypothesis | Defines `CMS - CMT`, expected funding spread, excess spread, z-score, DV01 matching, volatility scaling, and maturity ranking | This is the economic target that later agents and full backtests should approach |
| Current signal | Uses rolling residuals of Eris swap-futures prices against Treasury-futures prices | This is a useful proxy experiment, not yet the stated excess-spread strategy |
| Maturities | Code supports 2Y and 5Y; hypothesis also names 10Y and 30Y | Treat 10Y/30Y as a later expansion after 2Y/5Y correctness is proven |
| Historical data | Current top-level derived CSVs range from 4 to 99 columns; cache contains 1,474 Eris files and public-rate/vendor files | Define narrow dataset contracts and separate source, canonical, and result data |
| Quantt/R2 | `r2_database_names.py` inventories objects to `r2_objects.csv` | Object discovery exists; selective ingestion, schema validation, and lineage do not |
| Backtest | Uses contract price changes and supports configurable costs, but both cost defaults are zero | Separate naive fixed assumptions from realistic bid/ask, commissions, roll, and slippage |
| Treasury master | Public continuous symbols and proxy DV01 are explicitly labelled research limitations | Do not promote realistic results until executable contract and CTD assumptions are validated |
| Working tree | Existing user changes include `.gitignore`, deletion of `cloudflare_r2_test.py`, and untracked `r2_database_names.py` | Every task must preserve these unrelated changes |

## Target project shape

The exact migration occurs in small phases. The intended responsibility
boundaries are:

```text
strategy/
  models.py                 immutable input/output records and enums
  spread.py                 economic and executable spread calculations
  signal_generation.py      causal entries, exits, ranking, and state transitions
  position_sizing.py        DV01-neutral target quantities and volatility scaling
  risk_signals.py           limits, flattening decisions, and block reasons
  costs.py                  naive and observed transaction-cost calculations
  portfolio.py              combine maturity targets without broker calls

data_pipeline/
  contracts.py              CSV schemas, units, unique keys, and validation
  quantt_source.py          selective historical reads from Quantt/Cloudflare
  public_sources.py         explicitly labelled research fallbacks
  ibkr_paper_source.py      paper quote, order, fill, and position capture
  canonicalize.py           source-specific data to canonical narrow CSVs
  manifests.py              row counts, time ranges, hashes, and provenance

backtesting/
  engine.py                 causal replay of the complete strategy
  assumptions.py            naive and realistic scenario definitions
  accounting.py             fills, positions, P&L, costs, and equity
  reports.py                compact metrics, trades, daily results, and diagnostics

agents/
  shared/                   paper-only runner, telemetry, reconciliation, and safety
  agent_0/                  random baseline
  agent_1/ ... agent_10/    one approved incremental behavior per agent

tests/
  strategy/                 equations, signals, sizing, risk, and invariants
  data_pipeline/            schemas, causality, uniqueness, lineage, and fixtures
  backtesting/              fills, accounting, costs, rolls, and no-lookahead
  agents/                   broker fakes, safety, reconciliation, and behavior deltas

data/
  source/                   source-specific, narrow, immutable CSV captures
  canonical/                strategy-ready CSVs with stable schemas
  paper/                    IBKR paper quotes, orders, fills, and positions
  results/
    backtests/              complete-strategy outputs only
    agents/                 one directory per paper-agent run
  manifests/                provenance and validation summaries
  cache/                    vendor response cache, excluded from canonical inputs
```

The migration must not move or rewrite the existing `data/` tree until the
inventory, schema proposal, and migration preview pass a manual gate.

## Workstreams and dependency order

```text
Reproducible baseline
        |
        v
Economic equations --> Required data matrix --> Canonical data
        |                                      |
        +------------------+-------------------+
                           v
                    Shared strategy core
                      /             \
                     v               v
          Complete-strategy      Shared paper-agent
             backtests              platform
                     \               /
                      v             v
                    Evidence and attribution
```

The data contracts depend on the equations because fields should exist only
when the strategy, accounting, risk controls, or audit trail actually consumes
them. Agents depend on the shared strategy core so later signals are not copied
into agent-specific files. Both backtests and agents depend on canonical data
and the same strategy decisions.

## Phase roadmap

### Phase 0: Reproducible baseline

**Purpose:** Make the current repository runnable and establish facts before
changing strategy behavior.

**Execute:** Prompts `P00`, `P01`, and `P02`.

**Deliverables:**

- Repository baseline report, including dirty-worktree preservation notes.
- A supported Python version and dependency manifest derived from imports.
- One documented test command that works from a clean environment.
- Passing or explicitly recorded failing results for current tests and
  self-checks.
- Agent 0 characterization tests using fake broker objects only.
- One authoritative Agent 0 weekly order-count setting approved by the user.

**Manual gate:** `MG0` and `MG1`.

**Exit criteria:** A fresh environment can run the suite, Agent 0 cannot reach a
non-paper account in tests, and historical documentation disagreement is
resolved without changing Agent 0 behavior accidentally.

### Phase 1: Turn the hypothesis into an executable research specification

**Purpose:** Replace ambiguous prose with equations, units, timestamps, leg
directions, and hand-worked examples before requesting more data or coding new
signals.

**Execute:** Prompts `P10` and `P11`.

**Deliverables:**

- Exact economic spread, funding spread, expected funding, excess-spread,
  cost-buffer, z-score, entry, exit, and reverse-trade equations.
- Exact DV01 hedge and futures-basket P&L equations.
- Quote-convention and sign table for each instrument.
- Causal decision clock and observation-lag rules.
- Two hand-calculated golden examples per direction and one flattening example.
- Field-by-field source coverage matrix for Quantt, public research sources,
  and IBKR paper data.
- Explicit classification of every input as observed, derived, assumption, or
  unavailable.

**Manual gate:** `MG2`. The user must approve equations, signs, units, and
examples before implementation.

**Exit criteria:** Two independent reviewers can reproduce every hand
calculation and agree which trade direction each example produces.

### Phase 2: Inventory and contract the data

**Purpose:** Decide what data is necessary, where it belongs, and how it is
validated before moving existing files.

**Execute:** Prompts `P20` and `P21`.

**Deliverables:**

- Inventory of current CSVs, caches, R2 objects relevant to this strategy, date
  coverage, keys, units, missingness, duplicates, and consumers.
- Column lineage from source through canonical data to signal, risk, and P&L.
- Approved narrow CSV schemas from `PROJECT_CONTRACTS.md`.
- Migration preview mapping every existing artifact to keep, regenerate,
  archive, or supersede.
- Retention decision for raw vendor caches, which are not canonical strategy
  inputs.

**Manual gate:** `MG3`. No bulk move or rewrite occurs before approval.

**Exit criteria:** Every canonical column has one reason to exist, one unit, one
source or derivation, one unique key, and at least one named consumer.

### Phase 3: Build canonical Quantt and IBKR-paper ingestion

**Purpose:** Produce narrow, validated CSVs through source adapters without
leaking source-specific details into strategy code.

**Execute:** Prompts `P22`, `P23`, and `P24`.

**Deliverables:**

- Selective, read-only Quantt/Cloudflare historical ingestion.
- IBKR paper quote/order/fill/position recorder with paper-account enforcement.
- Deterministic canonicalization and manifest generation.
- Schema, uniqueness, ordering, timezone, unit, coverage, and freshness checks.
- Dry-run migration followed by an approved migration of current data.

**Manual gate:** `MG4`. Inspect representative source and canonical samples by
hand before strategy use.

**Exit criteria:** Re-running ingestion on unchanged inputs produces identical
canonical files and manifests; no secret or unnecessary source column is
written.

### Phase 4: Build the shared strategy core

**Purpose:** Implement the hypothesis once as pure functions usable by both
full-strategy replay and IBKR paper agents.

**Execute:** Prompts `P30` through `P35`.

**Deliverables:**

- Stable models and typed interfaces.
- Economic spread and executable-basket calculations.
- Causal signal state machine and maturity ranking.
- DV01-neutral sizing, volatility targeting, portfolio limits, and flattening.
- Naive and observed cost models.
- Golden, property, causality, and invariant tests.
- A frozen supported-maturity scope. Add 10Y/30Y only after their rates,
  executable contracts, DV01, costs, and liquidity inputs pass the same gates
  as 2Y/5Y.

**Manual gate:** `MG5`. Compare code output with Phase 1 hand examples.

**Exit criteria:** Strategy tests have no file, network, clock, or broker
dependency; the same input records always produce the same decisions. If
10Y/30Y remain unavailable, every downstream run is labelled
`complete_2y_5y`, not “complete four-maturity strategy.”

### Phase 5: Build the naive complete-strategy backtest

**Purpose:** Test the whole strategy mechanics using declared constant
bid/ask, commission, slippage, and funding assumptions.

**Execute:** Prompt `P40`.

**Deliverables:**

- Causal replay engine using the shared strategy core.
- Explicit fill timing and fixed naive assumptions.
- Separate daily, trades, positions, and summary CSVs.
- Accounting identities and no-lookahead tests.
- Diagnostics for exposure, turnover, costs, missing inputs, and blocked risk.

**Manual gate:** `MG6`. Review a short date window trade by trade.

**Exit criteria:** Cash, positions, costs, P&L, and equity reconcile exactly on
golden scenarios; results are labelled “naive” and never presented as
executable evidence.

### Phase 6: Build and challenge the realistic complete-strategy backtest

**Purpose:** Replace fixed assumptions with time-varying observable bid/ask,
fees, slippage, funding, contract rolls, and liquidity where validated data
exists.

**Execute:** Prompts `P41` and `P42`.

**Deliverables:**

- Realistic scenario with documented fallbacks and conservative handling of
  missing execution data.
- Walk-forward and subperiod reports.
- Sensitivity tests for costs, delay, thresholds, lookbacks, liquidity, and
  roll assumptions.
- Comparison of naive and realistic results using identical signals.
- Statistical and economic review that reports uncertainty and avoids
  selecting a single best parameter run.

**Manual gate:** `MG7`.

**Exit criteria:** The realistic result survives accounting review and its
limitations are explicit. A positive result remains a research finding, not
proof of alpha.

### Phase 7: Create a reusable paper-agent platform and freeze Agent 0

**Purpose:** Separate common paper execution, telemetry, and safety from agent
policy, then preserve Agent 0 as the random control.

**Execute:** Prompts `P50` and `P51`.

**Deliverables:**

- Paper-only shared runner and IBKR adapter.
- Unique agent IDs, client IDs, order references, run IDs, and isolated logs.
- Narrow quote, decision, order, fill, position, error, and heartbeat CSVs.
- Position reconciliation, stale-data checks, kill switch, flattening, session
  loss, exposure, order-rate, and working-order controls.
- Deterministic Agent 0 policy tests and fake-broker integration tests.
- Frozen Agent 0 behavior version and baseline paper-run protocol.

**Manual gate:** `MG8`. User reviews configuration and intentionally starts the
paper run; no agent prompt submits orders automatically during development.

**Exit criteria:** Tests prove that production routing is structurally absent,
every decision is attributable to one run, and a repeated random seed produces
the same planned decisions.

### Phase 8: Run the incremental agent ladder

**Purpose:** Attribute changes in paper behavior and outcomes to one new
component at a time.

**Execute:** Prompts `P52` through `P61`, one prompt and one paper observation
window at a time.

| Agent | Only new behavior | Primary question |
|---|---|---|
| Agent 0 | Uniform random instrument, side, timing, and bounded quantity | Can the paper execution and telemetry system operate reliably? |
| Agent 1 | Approved position-flattening rule | Does inventory control reduce unintended exposure and tail risk? |
| Agent 2 | Causal 5 bp market-spread movement trigger over the approved decision interval | Does a minimal market movement rule improve outcomes over inventory control alone? |
| Agent 3 | DV01-neutral paired leg sizing | Does hedging reduce outright rate exposure without destroying execution quality? |
| Agent 4 | Economic raw swap-spread threshold | Does the hypothesis-aligned spread direction improve on price movement alone? |
| Agent 5 | Expected funding-spread adjustment | Does funding information add incremental value after costs? |
| Agent 6 | Rolling excess-spread z-score with hysteretic entry and exit | Does standardized dislocation timing improve selectivity? |
| Agent 7 | Volatility targeting and portfolio risk caps | Does risk normalization improve the return-to-risk distribution? |
| Agent 8 | Bid/ask, fee, liquidity, freshness, and roll filters | Do executable-opportunity filters remove false paper alpha? |
| Agent 9 | Cross-maturity opportunity ranking | Does capital allocation to the strongest approved maturity improve efficiency? |
| Agent 10 | Complete approved hypothesis configuration | Does the integrated paper strategy behave like the complete strategy specification? |

An agent may be split into two agents if its “only new behavior” cannot be
implemented and tested as one isolated delta. Agent numbers then advance; no
phase may bundle two unmeasured additions merely to preserve this table.

**Mandatory rule:** Agent N cannot begin until Agent N−1 has a frozen config,
completed run manifest, reviewed output, and signed promotion record.

**Manual gate:** `MG9` after every agent. Paper runs are deliberate user actions.

**Exit criteria:** Each agent has reproducible code, config, tests, manifests,
and an evidence report comparing it with its immediate predecessor.

### Phase 9: Evaluate incremental evidence and reconcile research tracks

**Purpose:** Decide what has been learned without conflating backtest evidence,
paper fills, execution quality, and random chance.

**Execute:** Prompt `P70`.

**Deliverables:**

- Paired predecessor comparison for every agent.
- Full-strategy naive versus realistic backtest comparison.
- Paper Agent 10 versus realistic-backtest expectation comparison.
- Results split into signal contribution, sizing/risk contribution, execution
  quality, costs, and data limitations.
- Negative and inconclusive findings retained, not discarded.
- Clear statement of which hypothesis claims are supported, contradicted, or
  unresolved.

**Manual gate:** `MG10`.

**Exit criteria:** The final research summary can be reproduced from immutable
run manifests and does not claim more than the evidence supports.

### Phase 10: Consolidate the repository

**Purpose:** Remove superseded code and artifacts only after the replacement
path is accepted.

**Execute:** Prompt `P80`.

**Deliverables:**

- Updated architecture and operator documentation.
- Archived, clearly labelled legacy outputs.
- Removal of confirmed dead code and duplicate writers.
- Clean test, lint, schema, and documentation checks.
- One command each for canonical-data refresh, naive backtest, realistic
  backtest, and paper-agent dry run.

**Manual gate:** Final repository review before deletion or archival.

**Exit criteria:** A new contributor can reproduce the research workflow from
the four master documents without reading historical chat.

## Project-level definition of done

The project has reached its intended outcome when:

1. Every pathway is permanently paper-only.
2. The economic hypothesis and executable implementation have approved
   equations, units, signs, and causal timestamps.
3. Quantt and IBKR paper adapters produce narrow, validated, provenance-rich
   CSVs.
4. The same pure strategy core is used by complete-strategy backtests and later
   paper agents.
5. Naive and realistic complete-strategy backtests reconcile and report
   limitations.
6. Agent 0 through the final hypothesis agent each add one auditable behavior.
7. Every paper run has immutable configuration, inputs, decisions, orders,
   fills, positions, errors, and summary evidence.
8. Manual and sub-agent reviews are recorded at crucial gates.
9. Results distinguish operational reliability, risk reduction, execution
   quality, and genuine incremental signal evidence.
10. The final conclusion may be positive, negative, or inconclusive; all three
    are valid outcomes when supported by reproducible evidence.
