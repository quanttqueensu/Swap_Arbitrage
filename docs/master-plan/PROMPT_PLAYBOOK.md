# Swap Arbitrage Prompt Playbook

Use these prompts in order. Start a fresh Codex task for a prompt when practical
so its context contains one bounded objective. Do not combine prompts or ask an
agent to “finish the whole master plan.”

## Instructions for the human operator

1. Confirm the previous prompt’s manual gate is signed in
   `VERIFICATION_GATES.md`.
2. Paste exactly one prompt block into Codex.
3. Answer questions about equations, risk limits, schemas, or external paper
   runs yourself; those are intentionally not delegated decisions.
4. Review linked files and command output before approving the next prompt.
5. Start IBKR paper runs yourself only after the relevant prompt has completed
   fake-broker tests and stopped at its manual gate.
6. If a prompt discovers that its prerequisite is false, stop and repair the
   prerequisite rather than silently expanding scope.

## Rules included in every prompt

Every worker must:

- read all four files under `docs/master-plan/` and any relevant existing
  design/plan before acting;
- inspect `git status` and preserve all unrelated user changes;
- keep the system permanently IBKR paper-only and reject any production path;
- use a written task plan and work only the named prompt;
- use test-first implementation for behavior changes;
- avoid network or broker activity unless the prompt explicitly authorizes a
  read-only source check;
- never submit an IBKR order during development or automated tests;
- prefer pure, focused functions and existing dependencies;
- after P40B creates `docs/TECHNICAL_DOCUMENTATION.md`, update it in the same
  change whenever the prompt changes architecture, interfaces, dependencies,
  APIs, specifications, equations, schemas, commands, outputs, or operations,
  and rerun every affected documented command;
- run focused tests for every affected subsystem;
- run the full repository suite at manual gates, phase boundaries, or when
  shared infrastructure/interfaces change;
- use the review depth defined in VERIFICATION_GATES.md;
- require specialist review only for relevant high-risk domains;
- keep reviewers read-only; one implementer owns edits at a time;
- resolve every review finding or record evidence that it is invalid;
- report exact commands and outcomes;
- stop at the named manual gate without beginning the next prompt.

## Phase 0 prompts

### P00 — Record a reproducible repository baseline

```text
Execute only master-plan prompt P00.

Read all files in docs/master-plan/ and inspect the repository without changing
strategy behavior, agent behavior, data, or external systems. Record:

1. git branch, recent commits, status, ignored relevant paths, and all existing
   user changes that must be preserved;
2. Python entry points, imports, dependencies, environment files, test files,
   self-check commands, and documentation;
3. code and documentation disagreements, especially Agent 0 weekly order
   counts and paper-account safeguards;
4. current data files, sizes, columns, row counts, date coverage, unique keys,
   duplicate headers/keys, and obvious lineage;
5. which current signals implement the economic hypothesis and which are only
   proxies;
6. current backtest fill timing, cost defaults, roll handling, and known source
   limitations;
7. current R2/Quantt and IBKR capabilities.

Write the evidence to docs/project-baseline-2026-07-26.md. Do not repair issues
in this prompt. Redact credential values and list environment variable names
only. Use read-only commands. If Python is unavailable, record the exact error.

Ask a fresh requirements reviewer to compare the baseline report with
docs/master-plan/MASTER_PLAN.md, then ask a fresh quality reviewer to check
claims against repository evidence. Correct the report, run git diff --check,
and stop at manual gate MG0 with a concise list of facts requiring user
decisions.
```

### P01 — Repair the Python environment and define one test command

```text
Execute only master-plan prompt P01 after MG0 is approved.

Use the P00 baseline to establish a reproducible local Python environment.
Derive dependencies from imports and actual execution; do not guess versions
that are not exercised. Select and document one supported Python minor version.
Add the smallest standard dependency manifest appropriate to this repository,
including separate development dependencies only if necessary. Never include
credentials.

First add a smoke check that imports the project entry points and fails in the
current broken environment. Then create a fresh environment, install from the
manifest, and make the smoke check and existing unittest suite runnable. Add
one documented canonical test command and one environment setup procedure.
Do not change a test merely to make an implementation pass.

Run import compilation, all discovered tests, each existing --self-check, and
git diff --check. Record dependency versions and command output in the P01
verification record. Ask a requirements reviewer to confirm reproducibility
and a quality reviewer to check dependency minimality and secret hygiene.
Stop at manual gate MG1; do not refactor strategy or agent code.
```

### P02 — Characterize and freeze current Agent 0 behavior

```text
Execute only master-plan prompt P02 after the P01 environment is approved.

Add automated characterization tests for agents/agent_0 using pure functions
and fake broker objects. Cover paper-account rejection, port/client settings,
random plan determinism, allowed instruments, side and quantity bounds,
activation window, contract allocation limits, margin reserve behavior,
order-reference idempotency, reconciliation, cancellation isolation, and the
rule that tests never connect to IBKR.

Do not choose among the conflicting 50-, 100-, and 250-order historical
documents. Demonstrate the current code value in a test and present the three
conflicting sources at manual gate MG1. After the user chooses the authoritative
weekly count, update code, SETTINGS.md, current tests, and current design
documentation consistently; retain superseded documents as historical records
with a clear superseded notice rather than rewriting history.

Ask a requirements reviewer to compare tests with current Agent 0 behavior and
a paper-execution safety reviewer to prove production routing is structurally
absent. Run focused agent tests, the full suite, compilation, and diff checks.
Stop at MG1 with no broker connection and no order submission.
```

## Phase 1 prompts

### P10 — Validate the economic and executable equations

```text
Execute only master-plan prompt P10 after MG1 is signed.

Treat docs/master-plan/PROJECT_CONTRACTS.md as the proposed equation contract.
Verify quote conventions, contract multipliers, leg directions, DV01
definitions, roll behavior, and the economic swap-spread interpretation against
authoritative primary sources for the exact Eris and Treasury instruments.
Clearly separate the cash-market hypothesis CMS-CMT from an executable futures
basket. Do not claim that subtracting futures prices measures a swap spread.
Fully validate the 2Y and 5Y executable mappings first. Record 10Y and 30Y
instrument candidates and limitations, but do not require their executable
golden tests or call them supported before P35.

Create docs/research/strategy-equations.md containing:
- a frozen strategy_spec_version and a parameter table;
- every equation with units and observation timestamps;
- traditional and reverse leg-direction tables;
- exact conversion between source quotes, rate/spread values, contract P&L,
  costs, and DV01-neutral quantities;
- the approved causal funding expectation estimator;
- the approved decision interval used by Agent 2's 5 bp trigger;
- the funding horizon and weights; z-score lookback, minimum observations,
  missing-observation rule, standard-deviation convention, and entry/exit
  thresholds; economic entry buffer; stale-data rules; exit and direct-reversal
  inequalities; signal decision time and earliest permissible fill time;
  integer hedge search range, rounding and deterministic tie-breaking;
  residual-DV01 tolerance; target-versus-rounded DV01 cost denominator;
  volatility, signal-strength, and liquidity scale formulas; and
  cross-maturity ranking score and deterministic tie-breaking;
- two numerical hand-worked examples in each direction;
- one entry, exit, reversal, roll, and risk-flatten example;
- a list of unavailable inputs and the labels required for proxies; and
- primary-source citations with URL, document title/version or publication
  date when available, and access date.

Store the numerical cases in
tests/fixtures/strategy_equation_cases.json. Add
tests/test_strategy_equation_spec.py to validate fixture completeness, units,
directions, arithmetic, symmetry, turnover, timestamps, and the parameter-table
reference without importing production strategy code. These specification
tests must pass; do not commit intentionally failing implementation tests
between Phase 1 and P31. P31 will load the same fixtures when it writes the
failing production-function tests.

Ask two independent financial-equations/sign reviewers to recompute every
example and a separate causality reviewer to inspect timestamps, publication
lags, revisions, and earliest fill times. Resolve their findings and stop at
the provisional P10-EQ checkpoint for user approval of the equation package.
P10-EQ authorizes P11 only; it is not MG2 and does not authorize strategy
implementation. Do not implement signals in this prompt.
```

### P11 — Map each required field to an approved source

```text
Execute only master-plan prompt P11 after the provisional P10-EQ checkpoint is
approved.

From the approved equations, build
docs/research/data-source-coverage-matrix.md. Include one row per required
field: semantic name, unit, frequency, effective date, source observation time,
publication time, decision-time availability, availability lag, timezone,
revision/vintage policy, stale threshold, history needed, primary FRED series
or CME Group dataset, IBKR paper field when applicable, approved fallback,
observed/derived/assumed/unavailable classification, proxy label, license or
access restriction, validation rule, and exact strategy/accounting consumer.

Treat the existing R2 inventory as historical metadata only. Do not call
Cloudflare or plan a Quantt/R2 dependency. Use authoritative FRED, CME Group,
and IBKR documentation for field meaning, publication timing, revisions,
contract conventions, and access limits.

Explicitly resolve whether the approved FRED, CME Group, and IBKR inputs supply
maturity-matched swap rates, Treasury yields, repo, bid/ask, contract metadata,
and DV01. Mark EFFR-SOFR, continuous futures roots, fixed hedge ratios, and
price regressions as proxies where appropriate. Fully assess 2Y and 5Y. Record
10Y and 30Y as candidates
with exact blockers until P35; their absence does not invalidate a complete
2Y/5Y Phase 1 package.

Ask a data-source reviewer to verify sample fields against source docs and a
strategy reviewer to prove every equation input has a matrix row. Stop at MG2
if the matrix and equations remain consistent. If a source fact changes an
equation, parameter, sign, unit, timestamp rule, or proxy interpretation,
return to P10: update the equation document, parameter table, machine-readable
fixtures, and passing specification tests; repeat the affected independent
reviews and obtain a new P10-EQ checkpoint before completing P11.

Request final MG2 only when the reconciled equation package and source matrix
share the same strategy_spec_version. Report which complete-strategy tests are
possible with current data and which remain blocked. MG2 approval authorizes
Phase 2; it does not authorize strategy implementation.
```

## Phase 2 prompts

### P20 — Audit current data and column lineage

```text
Execute only master-plan prompt P20 after MG2 is signed.

Build a read-only data audit with scripts that can be rerun. For every current
CSV and relevant cached/source dataset, report path, purpose, source, schema,
units where known, size, rows, time range, unique key, duplicate headers,
duplicate keys, sort order, missingness, constant columns, exact duplicate
columns, and code readers/writers. For large cache trees, inspect metadata and
representative samples without loading everything into memory.

Trace every column in current raw_price_data.csv, signal_data.csv,
risk_data.csv, and backtest outputs to its writer and consumers. Classify each
as source, canonical, feature, decision, risk, accounting, diagnostic, or
unused. Flag the current widening from 24 to 40 to 72 to 99 columns and
identify columns copied without a downstream consumer.

Write docs/data/current-inventory.md and
docs/data/current-column-lineage.csv. Do not move, delete, or rewrite data.
Ask a data-quality reviewer to reproduce summary counts and a code-lineage
reviewer to verify readers/writers with repository search. Stop at MG3 with
the audit and discrepancies.
```

### P21 — Design narrow schemas and a migration preview

```text
Execute only master-plan prompt P21 after P20 is reviewed.

Compare the P20 audit with the schemas in PROJECT_CONTRACTS.md. Propose only
changes justified by an approved consumer. For each canonical CSV, freeze path,
schema version, columns, units, types, unique key, ordering, missing-value
policy, update frequency, retention, and validation. Prefer long narrow market
data and separate decisions/trades/positions/results over wide pipeline copies.

Create docs/data/canonical-schemas.md and
docs/data/migration-preview.md. The preview must map every existing data file
to one action: keep immutable source, regenerate into a named canonical file,
archive as a labelled legacy result, or supersede after validation. Include
before/after row and column expectations and recovery steps. It must not
perform the actions.

Add failing schema-contract tests against small fixtures. Ask a schema reviewer
to check keys/units and a migration-safety reviewer to verify that no source or
user file would be destroyed. Stop at MG3. Do not perform a bulk move, rewrite,
or deletion.
```

## Phase 3 prompts

### P23 — Implement the IBKR paper data recorder

```text
Execute only master-plan prompt P23 after the source schemas are approved.

Implement an IBKR adapter that can record the exact paper quote, order, fill,
position, and connection fields approved in PROJECT_CONTRACTS.md. Broker-facing
code must enforce paper port/account rules before requesting data or accepting
an OrderIntent. The data recorder must use UTC, stable instrument IDs, narrow
CSV schemas, run IDs, and append/reconciliation logic that is idempotent.

Write tests first using a fake IB object. Cover paper-account rejection,
production-port absence, quote normalization, invalid/crossed quotes, stale
timestamps, duplicate callbacks, partial fills, commissions, reconnect
reconciliation, atomic logs, and secret/account redaction. The default
development path is dry-run and cannot submit an order.

Ask a broker-safety reviewer to trace every path that could submit or cancel
and a data reviewer to compare CSV output with the approved schema. Run only
fake-broker tests. Hand the reviewed evidence to P24; do not request MG4 until
P24's canonical data and schema evidence are also ready. The user, not the development agent,
performs any later IBKR paper connectivity check.
```

### P24 — Migrate data through a verified dry run

```text
Execute only master-plan prompt P24 after MG3 approves the schemas and
migration preview and P23 passes its fake-broker safety and schema reviews.

Implement canonicalization and schema validation using only the approved FRED,
CME Group, IBKR, and existing local source artifacts. Do not add
Quantt/Cloudflare ingestion. Write validated canonical partitions directly to
`data/rates/`, `data/futures/`, `data/market/`, and `data/contract_risk/`; keep
original inputs under `data/raw_data/`. Demonstrate deterministic output when
rerunning unchanged input and never coerce an invalid field silently.

Ask a data-quality reviewer to rerun validations and a migration reviewer to
check that all resolved paths stay inside the repository data directory.
Stop at MG4 with the five-folder durable data layout and schema evidence ready
for inspection.
```

## Phase 4 prompts

### P30 — Create immutable strategy models and interfaces

```text
Execute only master-plan prompt P30 after canonical data passes MG4.

Create the minimal strategy package boundaries from PROJECT_CONTRACTS.md.
Start with tests for immutable MarketSnapshot, SpreadObservation,
SignalDecision, TargetPosition, RiskDecision, and OrderIntent records. Validate
units, UTC timestamps, direction/state enums, nonnegative absolute quantities,
paper_only=True, immutable reason codes, and stable serialization needed for
CSV logs.

Do not implement equations, rolling signals, broker adapters, or a framework
beyond these consumed interfaces. Existing code may continue to run while
adapters are introduced later. Document exact signatures and dependencies.

Ask an interface reviewer to compare every field with PROJECT_CONTRACTS.md and
a simplicity reviewer to identify unnecessary abstraction. Run focused tests,
the full suite, compilation, and diff checks. Stop at MG5 with interface
examples and no behavior migration.
```

### P31 — Implement spread, funding, cost-buffer, and basket equations

```text
Execute only master-plan prompt P31 after P30 and the referenced
strategy_spec_version has final MG2 approval.

Load every hand-worked case from
tests/fixtures/strategy_equation_cases.json and write failing
production-function tests without changing the approved fixture values.
Implement pure functions in strategy/spread.py for unit conversion, fixed swap
spread, funding spread, causal expected funding, gross excess spread,
directional cost buffer, net opportunity, DV01 hedge quantities, residual
DV01, and basket P&L. Use the exact approved strategy_spec_version, sign,
cost-base, and rounding conventions.

Add property tests or systematic table tests for symmetry of traditional and
reverse directions, unit conversion, zero/negative invalid DV01, rounding
bounds, roll turnover, and missing inputs. Do not read files, use the clock,
calculate rolling z-scores, or create orders.

Ask an equations reviewer to recompute outputs independently and a numerical
reviewer to inspect rounding and tolerance choices. Stop at MG5 with test
output compared line by line to the hand examples.
```

### P32 — Implement causal signal generation and state transitions

```text
Execute only master-plan prompt P32 after P31 passes MG5 equation comparison.

Write failing tests for warm-up, causal rolling mean/std, zero variance,
positive and negative entry, economic eligibility, exit hysteresis, direct
reversal as exit plus entry, missing/stale data, and cross-maturity ranking.
Prove causality by perturbing future rows and showing prior decisions do not
change.

Implement strategy/signal_generation.py using only approved SpreadObservation
inputs and explicit state. Retain the current price-residual method as a
labelled legacy proxy until later consolidation; do not call it the complete
strategy. Emit reason codes and feature values used for every decision.

Ask a requirements reviewer to map each transition to the equation spec and a
lookahead reviewer to audit rolling windows, sorting, timestamp boundaries,
and ranks. Stop at MG5 with a compact decision trace for the golden series.
```

### P33 — Implement position sizing and risk signals

```text
Execute only master-plan prompt P33 after P32 is approved.

Write failing tests for base target DV01, volatility/strength/liquidity scales,
integer hedge selection, residual DV01 minimization, gross/net portfolio caps,
contract caps, stale data, missing/nonpositive market fields, session
loss/drawdown, margin reserve, roll restrictions, reconciliation mismatch,
scheduled flattening, emergency flattening, and immutable reason codes.

Implement pure strategy/position_sizing.py and strategy/risk_signals.py.
Functions may consume snapshots, positions, limits, and signal decisions but
must not import IBKR, read files, use wall-clock time implicitly, or submit
orders. Make flattening a decision output.

Ask a risk reviewer to challenge limit interactions and a property reviewer to
verify that tighter limits never increase risk. Stop at MG5 with boundary-value
evidence and hand-checked hedge examples.
```

### P34 — Implement cost models and portfolio composition

```text
Execute only master-plan prompt P34 after P33 is approved.

Write failing tests for fixed naive costs, observed directional bid/ask costs,
commissions, slippage, financing, roll close-and-open turnover, missing-cost
behavior, portfolio maturity selection, and the rule that cross-maturity
ranking cannot violate risk limits.

Implement strategy/costs.py and strategy/portfolio.py. Naive and observed cost
models must implement one interface and return itemized dollar and
DV01-normalized costs. Missing realistic inputs block an opportunity or invoke
an explicitly configured conservative fallback; they never become zero.

Ask an accounting reviewer to verify cost units and a portfolio reviewer to
test competing maturity targets. Run the full Phase 4 suite and stop at MG5
with one end-to-end pure strategy example. Do not build the backtest engine.
```

### P35 — Freeze the supported maturity universe

```text
Execute only master-plan prompt P35 after P34 is approved.

Use the source-coverage matrix and canonical-data manifests to evaluate 2Y,
5Y, 10Y, and 30Y separately. A maturity is supported only when its
maturity-matched swap rate, Treasury rate, executable paper contracts,
contract multipliers, signed rate sensitivity/DV01, bid/ask or approved naive
costs, liquidity fields, roll rules, and golden equation tests have passed MG2
through MG5.

Parameterize the shared strategy tests across every supported maturity. Add 10Y
or 30Y only through existing interfaces; do not copy functions or fabricate
missing fields. If either maturity is not supported, write the exact blocker
and freeze the initial universe as 2Y/5Y. Require all downstream run IDs,
manifests, reports, and claims to use complete_2y_5y until a later version
passes the same gates.

Ask a data-coverage reviewer and a contract/equations reviewer to inspect each
maturity. Stop at MG5 with the approved maturity-scope record. Do not begin a
backtest in this prompt.
```

## Phase 5 prompt

### P40 — Build the naive complete-strategy backtest

```text
Execute only master-plan prompt P40 after MG5 approves the shared strategy
core.

Write failing golden tests for event timing, warm-up, order creation, next
eligible fill, entries, exits, reversals, partial or rejected simulated fills,
positions, fixed costs, financing, roll close/open, daily P&L, equity, drawdown,
and date-window behavior. Add a future-data perturbation test.

Implement a small causal replay engine under backtesting/ that invokes the
shared complete strategy. Configure constant, visibly named naive bid/ask,
commission, slippage, financing, and roll assumptions. Write separate manifest,
daily, decisions, orders, fills, trades, positions, and summary CSVs using
approved schemas. Label the run with the P35 maturity scope. Do not reuse
existing 99-column result shapes.

Ask an accounting reviewer to reconcile a short golden run and a causality
reviewer to inspect event ordering. Run a short representative data window only
after tests pass. Stop at MG6 and present every trade, cost, and position in
that window for manual reconciliation.
```

## Phase 6 prompts

### P40A — Audit the technical foundation

Execute only master-plan prompt P40A after the naive golden run passes MG6.

This prompt is read-only. Do not change implementation, tests, configuration, data, or existing documentation. The only file that may be created or updated is:

`docs/audits/technical-foundation-audit.md`

Preserve unrelated work. Do not connect to external market-data services or IBKR and do not submit, modify, or cancel orders.

## Context

Read:

* this prompt;
* the permanent project constraints;
* the target project shape;
* the MG6/MG6A requirements; and
* only the sections of `PROJECT_CONTRACTS.md` relevant to findings being investigated.

Consult other master-plan material only when necessary to resolve a concrete requirement.

## Audit scope

Review these subsystems:

1. environment and dependencies;
2. canonical data and schemas;
3. strategy and signal logic;
4. sizing and risk;
5. costs, accounting, and naive backtesting;
6. paper-agent architecture and safety boundaries;
7. tests and reproducibility;
8. documentation and onboarding.

For each subsystem, record:

* its current responsibility and main entry points;
* whether it matches the target architecture;
* material correctness or maintainability issues;
* important missing tests or documentation;
* concrete recommended actions.

Exclude `.git/`, virtual environments, worktrees, generated caches, vendor datasets, and immutable results from line-by-line review. Inspect only their interfaces, placement, retention rules, or manifests when relevant.

## Findings

Create a finding only when an action, user decision, or explicit deferral is warranted.

Each finding must include:

* stable ID;
* severity: critical, high, medium, or low;
* category;
* exact evidence;
* impact;
* smallest recommended action;
* validation method;
* proposed disposition:

  * safe cleanup;
  * approval-required structural change/deletion;
  * documentation-only;
  * defer.

Do not include speculative style improvements or unrelated refactoring.

For proposed deletions or structural rewrites, also record:

* affected consumers;
* risk;
* recovery method;
* verification required.

For a material ambiguity that prevents a safe recommendation, record:

* exact clarification question;
* plausible interpretations;
* recommended interpretation;
* affected behavior;
* consequence of deferral.

Do not infer unresolved product, strategy, or risk decisions.

## Technical verification

Reconcile findings involving equations, units, signs, timing, or accounting against `PROJECT_CONTRACTS.md` and approved golden examples.

Verify external API/package behavior against primary documentation only when:

* current runtime correctness depends on that behavior; or
* an audit finding specifically concerns an API/version assumption.

Record the source, relevant version, and verification date for such findings.

Performance work is out of scope unless an existing reproducible benchmark or measured performance problem demonstrates a material issue.

## Architecture output

Include:

* a concise current architecture map;
* a proposed target tree only where it differs materially from the current structure;
* an exact list of proposed deletions and structural rewrites requiring approval.

## Review

After completing the audit, perform one fresh read-only technical review checking:

* evidence quality;
* requirement coverage;
* scope discipline;
* unnecessary proposed changes.

Use an additional specialist review only for findings involving:

* mathematical/accounting correctness;
* broker safety; or
* version-sensitive external API behavior.

Correct evidence errors in the audit.

Stop at the MG6A authorization checkpoint.

The user must approve, reject, or defer every proposed deletion, structural rewrite, contract change, and unresolved material clarification before P40B begins.


### P40B — Apply approved cleanup and create technical documentation

Execute only master-plan prompt P40B after the MG6A authorization checkpoint records a disposition for every approval-required P40A finding.

Implement only:

* safe cleanup identified by P40A; and
* deletions, structural rewrites, or contract changes explicitly approved at MG6A.

Do not expand scope or infer answers to deferred clarifications.

Preserve unrelated work and all paper-only protections. Do not connect to external systems or transmit broker orders.

## Implementation

Before a behavior-sensitive change, add or identify a focused characterization/regression test demonstrating the behavior that must remain stable.

For each approved finding:

1. inspect all known consumers;
2. make the smallest approved change;
3. repair affected references;
4. run focused verification;
5. update the audit finding with its final disposition and evidence.

Comments should explain only non-obvious:

* invariants;
* units/signs;
* timing rules;
* safety rules;
* external API constraints.

Do not add comments that merely restate code.

Do not perform speculative performance optimization. A performance change requires an existing reproducible baseline measurement and an equivalent post-change measurement.

## Technical documentation

Create:

`docs/TECHNICAL_DOCUMENTATION.md`

This becomes the primary onboarding entry point for a capable contributor unfamiliar with the repository.

Use progressive detail:

### Quick start

* project purpose;
* paper-only boundary;
* environment setup;
* important commands;
* high-level architecture.

### System reference

* directory/component ownership;
* data flow;
* canonical data and provenance;
* strategy → sizing/risk → execution flow;
* backtest flow and outputs;
* Agent/paper execution lifecycle;
* testing and failure handling.

### Technical reference

* relevant equations, units, signs, and timing conventions;
* configuration and dependency requirements;
* external API/package facts that runtime behavior depends on;
* troubleshooting;
* glossary.

Summarize and link to authoritative contracts instead of duplicating them. Record source/version/verification dates only for volatile external facts actually relied upon by the project.

## Final verification

After all approved changes are complete:

* run focused tests for every changed subsystem;
* run the full approved repository suite;
* run applicable schema/documentation checks;
* search for broken references;
* run secret checks;
* run `git diff --check`;
* run `git status --short`;
* run every command presented as currently supported in `TECHNICAL_DOCUMENTATION.md`.

Perform one fresh consolidated repository-quality review of the final diff.

Add a specialist review only when the final changes affect:

* equations/accounting;
* broker safety;
* data schemas/migrations; or
* external API assumptions.

Resolve actionable findings and rerun affected verification.

Stop at the MG6A completion sign-off with:

* audit dispositions;
* approved-versus-actual cleanup summary;
* verification evidence;
* `docs/TECHNICAL_DOCUMENTATION.md`;
* remaining deferred issues.


## Phase 7 prompts

### P41 — Add the realistic complete-strategy scenario

```text
Execute only master-plan prompt P41 after the naive golden run passes MG6 and
the technical-foundation audit and remediation complete MG6A.

Define a realistic assumptions object using the approved observed bid/ask,
fees, funding, slippage, liquidity, contract, and roll fields. Write failing
tests showing that identical signals flow through naive and realistic models,
that worse execution cannot improve net P&L, and that missing required
execution data never becomes a free fill.

Implement realistic fill and cost behavior without copying strategy equations.
Record each fallback, blocked trade, stale quote, rejected fill, and roll
decision. Run naive and realistic scenarios on the same frozen input manifest
and configuration except for execution assumptions.

Ask an execution-model reviewer to challenge fill optimism and an accounting
reviewer to reconcile costs. Stop at MG7 with a trade-level naive-versus-
realistic comparison and all data limitations.
```

### P42 — Challenge the full-strategy backtests

```text
Execute only master-plan prompt P42 after P41 passes accounting review.

Create a predeclared robustness matrix covering walk-forward windows, calendar
subperiods, crisis/calm regimes, cost multiples, one-decision delay, threshold
and lookback neighborhoods, funding assumptions, liquidity filters, roll
assumptions, and removal of each strategy component. Do not optimize by
reporting only the best combination.

Add tests for deterministic run IDs/manifests, complete grid reporting, no
overlapping train/test leakage, and stable summary calculations. Produce one
report that includes every run, uncertainty intervals appropriate to the
sample, turnover, exposures, costs, drawdowns, active days, and negative or
inconclusive results.

Ask a statistical reviewer to inspect multiple-testing and sample-size claims,
a lookahead reviewer to re-audit splits, and a skeptical strategy reviewer to
list alternative explanations. Stop at MG7. Do not alter the strategy in
response to results inside this prompt.
```

## Phase 8 prompts

### P50 — Extract the shared paper-agent platform

```text
Execute only master-plan prompt P50 after the full-strategy core and canonical
paper schemas are stable.

Write failing fake-broker tests for run identity, unique client/order
references, connection validation, paper-account enforcement, quote freshness,
decision logging, intent conversion, submission dry-run, order/fill/position
reconciliation, retry idempotency, heartbeat, disconnection, working-order
limits, session risk, scheduled flatten, emergency flatten, and atomic logs.

Extract agents/shared/ without changing Agent 0 policy. Broker and clock
dependencies must be injected. The normal development command is dry-run; an
external paper submission requires an explicit user-started command and
approved configuration. Production ports/accounts/toggles must not exist.

Ask a paper-safety reviewer to trace every broker mutation and a compatibility
reviewer to prove Agent 0 decisions are unchanged. Stop at MG8 with fake-broker
evidence and a paper-run configuration preview.
```

### P51 — Freeze Agent 0 as the random control

```text
Execute only master-plan prompt P51 after P50 passes MG8 platform review.

Move only shared orchestration out of Agent 0; retain uniform random selection,
side, activation timing, and bounded quantity exactly as approved in P02.
Write tests proving Agent 0 imports no strategy signal, does not inspect
positions for its decision, produces reproducible plans from a seed, and emits
complete decision/order telemetry.

Create an immutable Agent 0 configuration record, run-manifest format, operator
checklist, and version label. Define the planned observation window and metrics
before any paper run. Use fake brokers for automated verification.

Ask a control-policy reviewer to search for hidden signals and a safety
reviewer to inspect paper-only routing. Stop at MG8. The user decides whether
and when to start the external Agent 0 paper run.
```

## Phase 9 agent prompts

Every agent prompt follows the same experiment sequence:

1. Freeze the predecessor’s code, config, run manifest, and evidence report.
2. Write tests showing the predecessor does not have the new behavior.
3. Implement only the named behavior through shared strategy components.
4. Prove all unrelated decisions match the predecessor on controlled fixtures.
5. Run fake-broker, safety, telemetry, and full repository tests.
6. Obtain requirements and quality reviews plus the named specialist review.
7. Pre-register paper window, universe, limits, and metrics.
8. Stop at MG9. The user starts the paper run.
9. After the window, ingest results without changing code and complete the
   predecessor comparison before considering the next agent.

### P52 — Agent 1: position flattening

```text
Execute only master-plan prompt P52 after Agent 0 has a signed run record.

Create Agent 1 by inheriting Agent 0 and adding only the approved position-
flattening rule. Random entries remain unchanged. Define flatten triggers,
priority, order intent, partial-fill handling, retry, reconciliation, and
reason codes from strategy/risk_signals.py. Tests must show no flatten when
already flat, correct signed close quantity, no accidental reversal, idempotent
retries, and new entries blocked while an urgent flatten is incomplete.

Follow the Phase 9 experiment sequence. Ask a position-risk specialist to
review edge cases. Stop at MG9; do not start IBKR or submit paper orders.
```

### P53 — Agent 2: causal 5 bp movement trigger

```text
Execute only master-plan prompt P53 after Agent 1 has a signed comparison.

Create Agent 2 by adding only the approved causal 5 bp market-spread movement
trigger over the decision interval frozen in strategy-equations.md.
The trigger replaces random entry timing/direction only to the extent approved;
all Agent 1 flattening and sizing behavior remains identical. Use bps, not raw
incompatible price-point subtraction.

Tests must cover exactly 4.99, 5.00, and 5.01 bp moves in both directions,
warm-up, stale/missing observations, repeated observations, causal timestamps,
and future-data perturbation. Follow the Phase 9 experiment sequence and ask a
market-data/units specialist to review the trigger. Stop at MG9.
```

### P54 — Agent 3: DV01-neutral paired sizing

```text
Execute only master-plan prompt P54 after Agent 2 has a signed comparison.

Create Agent 3 by adding only the approved DV01-neutral swap/Treasury paired
sizing from strategy/position_sizing.py. The 5 bp trigger and flattening remain
unchanged. Tests must cover contract signs, integer rounding, residual DV01,
caps, missing DV01, one-leg rejection, partial fills, hedge repair, roll
contracts, and the rule that invalid hedges create no new risk.

Follow the Phase 9 experiment sequence. Ask a DV01 and execution specialist to
review hedge direction and legging risk. Stop at MG9.
```

### P55 — Agent 4: economic raw swap-spread threshold

```text
Execute only master-plan prompt P55 after Agent 3 has a signed comparison.

Create Agent 4 by adding only the approved raw economic swap-spread threshold.
Use maturity-matched rate inputs and the fixed-spread equation; do not use a
raw swap-futures/Treasury-futures price difference. Preserve Agent 3 sizing,
flattening, and all safety controls.

Tests must cover units, positive/negative direction, threshold boundaries,
maturity mismatch, unavailable rate inputs, source freshness, and hand-worked
equation examples. Follow the Phase 9 experiment sequence. Ask an economic-
equations specialist to review. Stop at MG9.
```

### P56 — Agent 5: expected funding adjustment

```text
Execute only master-plan prompt P56 after Agent 4 has a signed comparison.

Create Agent 5 by subtracting only the approved causal expected funding spread
from the raw swap spread. Preserve every other behavior. Tests must cover
funding estimator warm-up, timestamps, units, positive/negative funding,
missing repo/reference fields, proxy labelling, and cases where funding changes
eligibility without changing the raw spread.

Follow the Phase 9 experiment sequence. Ask a funding-data specialist and a
causality reviewer to inspect the estimator. Stop at MG9.
```

### P57 — Agent 6: excess-spread z-score and hysteresis

```text
Execute only master-plan prompt P57 after Agent 5 has a signed comparison.

Create Agent 6 by adding only the approved causal rolling z-score, entry
threshold, and exit hysteresis to net economic eligibility. Preserve funding,
hedging, flattening, and base sizing. Tests must cover warm-up, zero variance,
entry boundaries, state persistence, exit, reversal turnover, missing values,
and future-data perturbation.

Follow the Phase 9 experiment sequence. Ask a time-series and lookahead
specialist to review rolling calculations and state transitions. Stop at MG9.
```

### P58 — Agent 7: volatility targeting and portfolio risk caps

```text
Execute only master-plan prompt P58 after Agent 6 has a signed comparison.

Create Agent 7 by adding the one approved risk-normalization package:
volatility scaling together with the portfolio caps required to make that
scaling safe. Do not add cost, liquidity, or ranking filters. Tests must cover
scale boundaries, calm/stressed inputs, causal lookbacks, gross/net DV01,
contract caps, session loss/drawdown, monotonicity of tighter limits, and
flattening after a breach.

Follow the Phase 9 experiment sequence. Ask a portfolio-risk specialist to
review. Stop at MG9.
```

### P59 — Agent 8: executable cost, liquidity, freshness, and roll filters

```text
Execute only master-plan prompt P59 after Agent 7 has a signed comparison.

Create Agent 8 by adding the approved executable-opportunity filter package:
directional bid/ask and fees, maximum slippage, quote freshness, minimum
liquidity, and roll restrictions. These are one execution-quality gate and do
not change the economic signal. Missing required data blocks new risk.

Tests must cover directional spread cost, cost-buffer boundary, stale/crossed
quotes, low size, missing fees, roll windows, conservative fallback, and the
rule that worse costs cannot create an eligible trade. Follow the Phase 9
experiment sequence. Ask an execution-quality specialist to review. Stop at
MG9.
```

### P60 — Agent 9: cross-maturity ranking

```text
Execute only master-plan prompt P60 after Agent 8 has a signed comparison.

Create Agent 9 by adding only cross-maturity ranking among economically
eligible, executable opportunities. Rank the approved maturities using the
frozen net score and deterministic tie-breaking. Respect portfolio limits and
do not add 10Y/30Y until their data, equations, contracts, and tests satisfy the
same gates as 2Y/5Y.

Tests must cover one/no/multiple eligible maturities, ties, missing data,
capacity constraints, a top-ranked blocked opportunity, and stability under
future-data changes. Follow the Phase 9 experiment sequence. Ask a portfolio-
allocation specialist to review. Stop at MG9.
```

### P61 — Agent 10: complete approved hypothesis configuration

```text
Execute only master-plan prompt P61 after Agent 9 has a signed comparison.

Create Agent 10 by composing the already approved shared components into the
complete hypothesis configuration. Do not invent a new signal. Diff the Agent
10 configuration against the complete strategy used in naive and realistic
backtests and explain every adapter-only difference caused by paper market
timing or broker mechanics.

Tests must run the same frozen MarketSnapshot sequence through the backtest
strategy call and Agent 10 policy and assert identical SignalDecision,
TargetPosition, and RiskDecision outputs before execution adaptation. Follow
the Phase 9 experiment sequence. Ask a full-strategy requirements reviewer, a
paper-safety reviewer, and an adapter-parity reviewer. Stop at MG9.
```

## Phase 10 and 11 prompts

### P70 — Evaluate evidence without changing the strategy

```text
Execute only master-plan prompt P70 after all completed agent windows and
backtests have immutable manifests.

Do not modify signals, thresholds, sizing, risk, costs, data, or paper-agent
behavior. Build a reproducible analysis that compares each agent only with its
immediate predecessor, the complete naive backtest with the realistic
backtest, and Agent 10 paper behavior with realistic-backtest expectations.

Report sample periods, observations, fills, exposure, gross/net P&L, costs,
turnover, drawdown, DV01, execution slippage, fill/reject rates, stale/blocked
decisions, risk events, return-to-risk metrics, uncertainty intervals, and
paired differences where timestamps permit. Separate operational reliability,
inventory/risk improvement, signal evidence, sizing effects, and execution
effects. Include negative and inconclusive results and disclose noncomparable
windows.

Ask a statistical reviewer, a data-lineage reviewer, and a skeptical research
reviewer to challenge every alpha claim. Write docs/research/final-evidence.md
with supported, contradicted, and unresolved hypothesis claims. Stop at MG10;
do not tune the strategy in response.
```

### P80 — Consolidate only accepted replacements

```text
Execute only master-plan prompt P80 after MG10 and explicit user approval of
the cleanup list.

Inventory code, docs, CSV writers, and data artifacts superseded by the
accepted architecture. Produce a deletion/archive preview with exact paths,
reasons, recovery method, and references proving no current consumer remains.
Do not remove anything until the user approves that preview.

After approval, use recoverable archive moves for historical research outputs,
remove only confirmed dead code, update operator and architecture docs, and
leave one documented command each for canonical refresh, naive backtest,
realistic backtest, fake-broker agent verification, and user-started IBKR paper
dry run. Run the complete suite, schema checks, manifest reproduction, secret
scan, and diff checks.

Ask a repository reviewer to search for broken references and a cleanup-safety
reviewer to compare actual changes with the approved preview. Report what was
archived or removed and how it can be recovered. Do not add any production
trading concept during final documentation.
```

## Completion response required from every prompt

The worker’s final response must include:

- the prompt ID completed;
- files created or changed;
- tests and checks run with pass/fail counts;
- sub-agent reviews performed and findings resolved;
- data or external systems read or changed;
- assumptions and remaining limitations;
- the manual gate now awaiting review;
- an explicit statement that no non-paper order path was added and no broker
  order was submitted during development.
