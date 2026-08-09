# Swap Arbitrage Verification Gates

This document defines what counts as evidence, when work must stop for manual
review, how sub-agents review changes, and how one paper agent may advance to
the next.

## Evidence hierarchy

From strongest to weakest:

1. Reproducible test or calculation from immutable inputs and a recorded code
   commit/config hash.
2. Independent manual calculation that agrees with the implementation.
3. Independent sub-agent review citing exact files, lines, commands, or rows.
4. Deterministic dry-run or fake-broker integration output.
5. IBKR paper observation with complete decision/order/fill/position telemetry.
6. Summary metric without underlying run artifacts.
7. Intuition or a positive P&L number by itself.

Lower levels never override a contradiction at a higher level. Backtest and
paper evidence are complementary:

- Backtests provide repeatable historical scenario evidence but depend on data
  and execution assumptions.
- Paper agents provide actual paper execution and current-market behavior but
  have short, path-dependent samples.
- Neither alone proves stable alpha.

## Standard verification layers

Every behavior change uses the applicable layers below.

### 1. Static and environment checks

- Supported Python version is active.
- Dependencies install only from the approved manifest.
- Modules compile.
- No credential, paper account ID, or secret value appears in source, logs,
  fixtures, errors, or CSVs.
- Repository diff has no whitespace errors.
- Unrelated user changes remain untouched.

### 2. Unit tests

Tests cover one pure function or record at a time:

- valid case;
- boundary values;
- invalid/missing values;
- units and signs;
- deterministic output;
- explicit reason codes.

### 3. Golden equation tests

Each approved equation has numerical examples calculated outside the
implementation. Tests assert:

- exact direction;
- rate/spread/price/DV01 units;
- expected quantities and P&L;
- traditional and reverse symmetry where applicable;
- roll and reversal turnover.

Phase 1 stores these values in
`tests/fixtures/strategy_equation_cases.json` and runs a passing
specification-validation test that does not import production strategy code.
Phase 1 never leaves intentionally failing tests in the repository. P31 reuses
the frozen fixtures when it introduces failing production-function tests and
then implements the approved equations.

Changing a golden number requires a new equation review, not a tolerance
increase.

### 4. Property and invariant tests

At minimum:

- future input changes cannot alter prior decisions;
- worse costs cannot improve net P&L or create eligibility;
- tighter limits cannot increase target risk;
- a flatten cannot increase absolute position;
- missing required realistic data cannot create a free trade;
- every fill traces to one order and every order to one decision;
- gross/net P&L and equity identities reconcile;
- rerunning unchanged inputs is deterministic.

### 5. Integration tests

Use small fixtures, fake clocks, fake data adapters, and fake brokers. Exercise:

- source to canonical CSV;
- canonical snapshot to strategy decision;
- decision to target/risk/order intent;
- order intent to simulated or fake-broker fill;
- fill to position/P&L/logs;
- reconnect and reconciliation.

Automated integration tests never connect to live FRED, CME Group, or IBKR
services.

### 6. Data-quality tests

Every CSV writer checks:

- exact header and schema version;
- parseable types and units;
- unique keys;
- chronological deterministic ordering;
- missingness policy;
- no duplicate or unused columns;
- no future observation relative to decision time;
- contract validity and roll boundaries;
- atomic replacement;
- matching manifest hashes, row counts, and coverage.

### 7. Backtest accounting tests

- Held quantity from the prior event earns the current price change.
- Entries, exits, reversals, resizing, and rolls charge correct turnover.
- Naive and realistic modes use identical strategy decisions for identical
  snapshots.
- Windowing handles warm-up and starting positions explicitly.
- Cash, realized/unrealized P&L, fees, financing, equity, and drawdown
  reconcile.
- Missing execution inputs block or invoke a recorded conservative fallback.
- Complete parameter grids and negative results remain in reports.

### 8. Paper-agent safety tests

- Only the approved paper port and account prefix are accepted.
- No production endpoint, port, account toggle, or override exists.
- Default command is dry-run.
- Fake-broker tests cover submit, cancel, partial fill, reject, disconnect,
  reconnect, stale data, margin failure, and flatten.
- New risk is blocked during reconciliation uncertainty.
- Order references are deterministic and idempotent.
- Working-order, quantity, DV01, session loss/drawdown, and margin-reserve
  controls act before submission.
- Logs survive restart and remain attributable to one run.

## Standard command evidence

Phase P01 freezes the exact environment command. Thereafter each verification
record includes:

```powershell
python -m unittest discover -s docs/tests -v
python -m unittest discover -s agents/agent_0/tests -v
python -m data_pipeline.historical_data.historical_data_builder --self-check
python signal_pipeline.py --self-check
python risk_pipeline.py --self-check
python backtest_engine.py --self-check
git diff --check
git status --short
```

Commands for superseded entry points are replaced only when their new
equivalents pass and the cleanup is approved. A verification record captures
the command, exit code, pass/fail count, runtime, and relevant error—not merely
“tests passed.”

## Sub-agent review protocol

Sub-agents are reviewers, not a substitute for manual project decisions.

### Roles

1. **Requirements reviewer:** Checks the implementation against the exact
   prompt, master contracts, and approved gate.
2. **Quality reviewer:** Checks correctness, simplicity, tests, failure
   handling, maintainability, and unintended changes.
3. **Specialist reviewer:** Used when the task involves equations, causality,
   data schemas, accounting, statistics, broker safety, risk, or migration.
4. **Skeptical research reviewer:** Challenges alpha claims, alternative
   explanations, data leakage, and selection bias.

### Required sequence

1. The implementer completes focused tests and records the diff.
2. A fresh requirements reviewer examines the prompt and diff read-only.
3. The implementer fixes or evidence-rejects each finding and reruns tests.
4. A fresh quality reviewer examines the corrected diff read-only.
5. A specialist reviewer examines the domain-specific evidence.
6. The implementer resolves findings, runs the full suite, and reports the
   manual gate.

Do not run two code-writing sub-agents against the same files. A reviewer may
create a report only when explicitly assigned a separate report path;
otherwise it returns findings without editing.

If multi-agent support is unavailable, the primary agent performs separate
fresh-pass reviews in the same order and records that independent dispatch was
unavailable.

### Requirements review prompt

```text
Review the current prompt implementation read-only. Read all four files under
docs/master-plan/, the exact prompt ID, the approved manual-gate record, and
the diff. Identify only concrete requirement gaps, scope violations, paper-only
violations, missing tests, or contradictions. Cite exact file/line or output
evidence. Do not edit files and do not suggest unrelated improvements. Return
PASS if no actionable finding remains.
```

### Quality review prompt

```text
Review the current diff read-only after requirements review. Check correctness,
causality, numerical units/signs, failure behavior, idempotency, data integrity,
test quality, unnecessary complexity, and preservation of unrelated changes.
For each actionable issue, cite exact evidence, impact, and the smallest safe
fix. Do not edit files. Return PASS if the implementation is ready for its
named manual gate.
```

### Statistical/research review prompt

```text
Review the frozen analysis read-only. Verify input/run comparability, sample
definition, causal timing, cost inclusion, uncertainty method, autocorrelation,
multiple comparisons, parameter selection, omitted negative results, and the
wording of every alpha claim. Recalculate representative metrics from raw run
artifacts. Distinguish operational, risk, execution, and signal evidence. Do
not tune the strategy or edit results.
```

### Broker-safety review prompt

```text
Trace every configuration and call path that can connect, submit, modify, or
cancel. Prove that only IBKR paper routing is representable, default execution
is dry-run, tests use fakes, limits run before submission, and reconciliation
blocks uncertain new risk. Search for production ports, production account
settings, bypass flags, generic account overrides, and hidden order paths.
Review read-only and cite exact evidence.
```

## Verification record format

Create one record per completed prompt under
`docs/verification/PROMPT_ID.md`. It contains:

```text
Prompt ID and objective
Code commit and config/schema/strategy versions
Phase 1 strategy_spec_version and golden-fixture hash when applicable
Files changed
Unrelated pre-existing changes preserved
Tests written before implementation
Commands, exit codes, and pass/fail counts
Golden/manual calculations checked
Data read or changed
External systems contacted
Requirements review findings and resolutions
Quality review findings and resolutions
Specialist review findings and resolutions
Known limitations
Manual gate requested
Paper-only assertion
Broker-order submission count during development: 0
```

## Manual gates

Manual gates are hard stops. “Looks reasonable” is insufficient; the named
evidence must be inspected.

### MG0 — Baseline facts

Inspect:

- repository status and preserved user changes;
- code/data/document inventory;
- current test/runtime failure evidence;
- proxy versus hypothesis classification;
- all unresolved contradictions.

Approve only when repository claims cite reproducible evidence. Approval
authorizes environment repair, not strategy or data migration.

### MG1 — Reproducible environment and Agent 0 baseline

Inspect:

- clean environment creation and dependency installation;
- complete test/self-check results;
- fake-broker proof that Agent 0 is paper-only;
- Agent 0 characterization tests;
- 50/100/250 weekly-order documentation conflict.

Record the authoritative weekly order count and reason. Approval freezes Agent
0’s current random behavior.

### P10-EQ — Provisional Phase 1 equation checkpoint

This is a manual checkpoint, not a numbered project gate. It occurs after P10
and before P11.

Inspect:

- the frozen `strategy_spec_version` and complete parameter table;
- 2Y/5Y quote conventions, instrument signs, multipliers, and roll behavior;
- all hand-worked examples and the passing specification-validation tests;
- two independent numerical/sign reviews; and
- the separate causality review of observation, publication, decision, and
  earliest-fill timestamps.

Approval authorizes P11 source mapping only. It does not approve MG2, authorize
strategy implementation, or mark any MG ledger row complete. If P11 changes an
equation, parameter, sign, unit, timestamp, or proxy interpretation, update P10
artifacts, repeat the affected reviews, and obtain a new P10-EQ approval.

### MG2 — Equations, signs, timestamps, and source coverage

Manually recalculate:

- traditional and reverse economic spread;
- funding adjustment;
- cost normalization;
- z-score observation window;
- swap/Treasury contract signs and DV01 hedge;
- entry, exit, reversal, roll, and flatten examples.

Inspect the complete parameter table and source-coverage matrix. Approve only
when:

- the P10 equation package and P11 matrix reference the same
  `strategy_spec_version`;
- every consumed field has an observed/derived/assumed/unavailable label;
- effective date, observation time, publication time, availability lag,
  timezone, revision/vintage policy, and stale threshold are explicit;
- proxy results cannot be mistaken for the full hypothesis;
- 2Y and 5Y have complete executable mappings, while 10Y and 30Y are labelled
  candidates with exact blockers until P35;
- two independent numerical/sign reviews and the causality review have no
  unresolved findings; and
- the approved repository test command remains green with the Phase 1
  specification tests included.

MG2 is requested only after P10, P11, and every required P10/P11 reconciliation
loop are complete. Approval authorizes Phase 2, not strategy implementation.

### MG3 — Data schemas and migration preview

Inspect:

- current inventory and lineage;
- one sample row for every proposed canonical schema;
- units, keys, missingness, partitioning, and consumers;
- every keep/regenerate/archive/supersede action;
- the five durable data folders and raw-input retention;
- direct canonicalizer output paths and validation rules.

Approval authorizes implementation of the approved layout. It does not
authorize external data reads or broker activity.

### MG4 — Validated source inputs and staged canonical data

Inspect:

- representative FRED and CME Group source samples against authoritative
  source documentation;
- IBKR paper-recording schema generated by fakes;
- credential/account redaction;
- canonical row counts, ranges, duplicates, and spot checks;
- deterministic rerun comparison;
- raw/canonical folder separation and absence of staging/manifests.

The user separately approves any read-only external connection. Approval of
MG4 permits strategy consumption of the validated canonical data.

### MG5 — Shared strategy core

Compare:

- every implemented equation with the manual examples;
- every state transition with a decision trace;
- future-data perturbation evidence;
- sizing/risk boundary tests;
- costs and portfolio composition;
- exact strategy interfaces used by both adapters.
- maturity-by-maturity evidence and the explicit `complete_2y_5y` label when
  10Y/30Y have not passed equivalent gates.

Approval freezes a strategy version. Any later equation change returns to MG2.

### MG6 — Naive backtest accounting

Select a short window and reconcile manually:

- observations and decision times;
- signal state;
- target and filled quantities;
- each leg’s price change and P&L;
- entries, exits, reversals, rolls, and fixed costs;
- daily P&L, equity, and drawdown.

Approval confirms mechanics only. It is not approval of alpha.

### MG6A — Technical-foundation audit, cleanup, and onboarding documentation

This gate has an authorization checkpoint and a completion sign-off.

At the authorization checkpoint, inspect:

- the audit scope, exclusions, repository map, and end-to-end traces;
- the ranked findings ledger and exact evidence for every finding;
- each material clarification question, its competing interpretations,
  recommended answer, affected behavior, and consequence of deferral;
- dependency and API requirements checked against primary sources, with
  package/API versions and verification dates;
- mathematical, timing, sign, unit, and accounting checks against the approved
  contracts and golden examples;
- the proposed target tree and every deletion or structural rewrite, including
  affected consumers, risk, recovery, and validation;
- which findings qualify as safe automatic cleanup and why; and
- the independent repository, API, mathematics/accounting, and documentation
  review findings.

Record approve, reject, or defer with a reason for every proposed deletion and
structural rewrite, and answer or defer every material clarification question.
Approval authorizes only the listed actions and recorded decisions. It does
not authorize external API connections, broker operations, unlisted
restructuring, or behavioral changes outside an explicitly approved contract
change.

At the completion sign-off, inspect:

- the approved action list against the actual diff;
- the final disposition and evidence for every audit finding;
- characterization tests protecting behavior-sensitive rewrites;
- deletion recovery instructions and proof that no current consumer remains;
- before/after evidence for every claimed performance optimization;
- comments for useful non-obvious reasoning without redundant narration;
- `docs/TECHNICAL_DOCUMENTATION.md` for newcomer readability, completeness,
  authoritative links, API versions/sources/dates, and paper-only wording;
- successful reproduction of every command documented as available;
- focused and full tests, schema/documentation checks, reference and secret
  checks, and diff/status evidence; and
- final repository-quality, mathematics/accounting, broker-safety, and
  newcomer-onboarding reviews.

Do not complete MG6A while an unresolved high-severity correctness, accounting,
causality, security, paper-routing, or reproducibility finding remains.
Completion authorizes the realistic-backtest phase. Every later phase must keep
the aggregate technical documentation synchronized with changes to
architecture, interfaces, dependencies, APIs, specifications, equations,
commands, or operating procedures.

### MG7 — Realistic backtest and robustness

Inspect:

- trade-level naive/realistic differences;
- observed versus fallback costs;
- blocked trades and missing data;
- roll and liquidity behavior;
- walk-forward/subperiod construction;
- full sensitivity grid and negative outcomes;
- statistical, causality, and skeptical reviews.

Classify findings as promising, adverse, or inconclusive without changing the
strategy in the review task.

### MG8 — Paper-agent platform and Agent 0 run

Inspect:

- broker-safety review;
- paper-only configuration preview;
- dry-run output;
- limits, kill switch, flattening, and reconciliation tests;
- unique run/client/order identities;
- narrow telemetry samples;
- frozen Agent 0 configuration and pre-registered observation plan.

The user starts the IBKR paper run manually. Development agents never start it.

### MG9 — Incremental agent promotion

Apply separately to every agent:

- predecessor code/config/run/evidence is frozen;
- exactly one new behavior is documented;
- controlled fixtures prove unrelated decisions are unchanged;
- fake-broker and safety suites pass;
- specialist review passes;
- observation window, universe, limits, metrics, and stopping rules are frozen;
- completed run has reconciled telemetry and no unexplained broker state;
- predecessor comparison separates behavior, risk, execution, and signal
  effects.

Record one decision:

- **Advance:** evidence quality is sufficient and the new behavior operates as
  designed.
- **Repeat unchanged:** data or operational sample is insufficient; rerun the
  same frozen version.
- **Stop/revise:** safety, correctness, or evidence contradicts the component;
  return to its design gate before another run.

Positive P&L is neither necessary nor sufficient for promotion.

### MG10 — Final evidence and cleanup

Inspect:

- every immutable run manifest;
- every immediate-predecessor comparison;
- naive versus realistic complete-strategy results;
- Agent 10 versus realistic expectation;
- negative/inconclusive findings;
- supported, contradicted, and unresolved hypothesis claims;
- proposed cleanup/archive list.

Approve cleanup separately from the research conclusion.

## Paper-run pre-registration

Before every externally started IBKR paper run, freeze:

- run ID, agent ID, strategy version, code commit, and config hash;
- immediate predecessor comparison target;
- paper account alias and unique IBKR client ID;
- instruments, maturities, trading session, timezone, and calendar dates;
- decision interval and feature-observation rules;
- random seed when applicable;
- starting-position requirement and reconciliation procedure;
- quantity, contract, gross/net DV01, order-rate, working-order, margin,
  session-loss, drawdown, and slippage limits;
- scheduled/end-of-run flattening policy;
- primary operational, risk, execution, and signal metrics;
- minimum usable decisions/fills for interpretation;
- conditions that halt the run early;
- rules for outages, stale data, partial fills, and market closures.

Do not alter the frozen configuration after viewing outcomes. A necessary
safety correction creates a new version and run ID; the interrupted run remains
in the record.

## Agent comparison contract

Compare Agent \(N\) only with Agent \(N-1\) first. Use:

### Operational metrics

- planned decisions, submitted orders, fills, rejects, cancels, partial fills;
- uptime, stale-data events, disconnects, reconciliation mismatches;
- unattributed orders/fills/positions, which must equal zero;
- risk blocks, flatten requests, and successful flatten completion.

### Exposure and risk metrics

- time-weighted gross and net DV01;
- maximum contracts and inventory duration;
- session loss, drawdown, and tail paper P&L;
- hedge residual and legging exposure;
- working-order and margin usage.

### Execution metrics

- bid/ask at decision and fill;
- implementation shortfall and slippage;
- fill/reject/cancel rates and latency;
- commissions and roll/turnover costs;
- blocked opportunities due to realistic constraints.

### Signal/economic metrics

- eligible decisions and completed trades;
- gross and net paper P&L;
- turnover and cost per unit of DV01;
- win/loss distribution and holding time;
- return-to-risk measures with the same denominator definition;
- incremental difference from the predecessor.

When runs share decision timestamps or can be replayed on identical paper
snapshots, use paired differences. When runs are sequential and market regimes
differ, state that the comparison is not controlled and avoid causal claims.

## Statistical evidence rules

1. Freeze metrics and comparison before observing a run.
2. Report sample size, active observations, trades, fills, and time coverage.
3. Use confidence intervals, preferably block/resampling methods that respect
   time dependence, rather than a p-value alone.
4. Report economic magnitude after costs, not only statistical significance.
5. Disclose every parameter/configuration tried and retain all outcomes.
6. Separate in-sample design, validation, and held-out/walk-forward periods.
7. Do not annualize a very short paper window without showing the unannualized
   result prominently.
8. Do not combine noncomparable agents into one performance curve.
9. Treat multiple maturity, threshold, and scenario comparisons as multiple
   tests and temper claims accordingly.
10. Prefer “supported,” “contradicted,” or “unresolved” to “proved.”

## Failure and rollback rules

- A failing prerequisite stops the current prompt.
- A schema or equation mismatch blocks downstream work.
- A source outage never licenses silent fallback.
- A realistic backtest with missing cost inputs blocks or records an approved
  conservative fallback.
- An unexplained paper order, fill, or position halts the run and triggers
  reconciliation.
- A risk or paper-routing safety failure prevents external paper operation
  until a new reviewed version passes MG8/MG9.
- Data conversion failures leave raw inputs untouched and require a reviewed
  correction.
- Historical run outputs and configs are immutable; corrections
  produce new versions.

## Gate ledger

Update this table only after the user explicitly approves a gate.

| Gate | Status | Evidence record |
|---|---|---|
| MG0 | Approved 2026-07-28 | Baseline report and P00 verification |
| MG1 | Approved 2026-07-29 | P01/P02 verification; authoritative 25/week (5/day) selected and approved |
| MG2 | Approved 2026-07-31 | P10 equation examples, source coverage, and verification |
| MG3 | Approved 2026-08-02 | P20/P21 inventory, 19 canonical schemas, durable five-folder layout, and verification |
| MG4 | Approved 2026-08-06 | P23 adapter verification, canonical data/schema review, and user approval |
| MG5 | Approved 2026-08-09 | P30-P35 verification; user approval with P35 blockers retained |
| MG6 | Not started | Naive backtest manual reconciliation |
| MG6A | Not started | Technical audit, approved cleanup, and onboarding documentation |
| MG7 | Not started | Realistic backtest and robustness reviews |
| MG8 | Not started | Paper-platform safety and Agent 0 pre-registration |
| MG9 | Not started | One record per incremental agent |
| MG10 | Not started | Final evidence and cleanup approval |

## Final verification standard

No phase is called complete unless:

- focused and full automated checks pass;
- manual calculations or samples match;
- required sub-agent reviews return no unresolved actionable findings;
- external reads/writes are disclosed;
- the named manual gate is recorded;
- no unintended file or data change remains;
- no broker order was submitted during development;
- no real-money configuration or path exists.
