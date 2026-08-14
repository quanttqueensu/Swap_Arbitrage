# Technical foundation audit (P40A)

Date: 2026-08-09
Gate basis: MG6 approved 2026-08-09 for synthetic mechanics only
Status: P40A/P40B complete; MG6A approved 2026-08-09 with recorded exceptions

## Scope and method

This audit reviewed maintained source, tests, configuration, schemas, and current
documentation across the eight P40A subsystems. It did not connect to FRED, CME,
IBKR, or any other external service, and it did not create, modify, or cancel an
order. Virtual environments, caches, worktrees, vendor data, and immutable results
were inspected only at their interfaces. The authorized MG6 ledger entry was
recorded before P40A as its prerequisite; this audit file is the only P40A change.

The working tree already contained unrelated, uncommitted Agent 0 and master-plan
changes. Findings below describe the current tree but do not assume those changes
may be discarded. No deletion is proposed merely because code is old or split
across legacy and target paths.

Verification used the bundled Python 3.12.13 runtime with the existing workspace
site-packages and stayed offline:

- `python -m unittest discover -s docs/tests`: 291 tests, passed.
- `python -m unittest discover -s agents/agent_0/tests`: 25 tests, passed.
- `python -m compileall -q backtesting strategy data_pipeline agents/agent_0 docs/tests`: passed.
- `signal_pipeline.py --self-check`, `risk_pipeline.py --self-check`, and
  `backtest_engine.py --self-check`: passed.
- `git diff --check`: failed on the pre-existing blank line at
  `agents/agent_0/config.py:121` and trailing whitespace at
  `agents/agent_0/run.py:300`.

No external API/version finding was created, so P40A did not need network or vendor
documentation. Installed workspace packages observed during the offline checks were
`ib-insync 0.9.86`, `eventkit 1.0.3`, `numpy 2.5.0`, and `pandas 3.0.3`; the ignored
workspace environment is not the authoritative dependency manifest.

## Current architecture map

```text
canonical CSVs -> data_pipeline/contracts.py validation
                         (no current canonical CSV -> MarketSnapshot adapter)
                                              |
                                              v
strategy/{spread,signal_generation,costs,position_sizing,risk_signals,portfolio}.py
                                              |
                          +-------------------+-------------------+
                          |                                       |
                          v                                       v
backtesting/{engine,assumptions,reports}.py             agents/agent_0/* -> IBKR paper

legacy local research path:
signal_pipeline.py -> risk_pipeline.py -> backtest_engine.py
```

The pure `strategy/` boundary and the historical/live data split match the target
architecture. `backtesting/` exists but has no canonical-data adapter and currently
mixes replay and accounting in one small module. That split is acceptable for now:
creating `backtesting/accounting.py` before a second consumer exists would add
structure without fixing a demonstrated problem. `agents/shared/` is also deferred
to the later multi-agent phase. The root legacy pipeline remains a tested consumer
and is not a deletion candidate until a canonical complete-strategy replacement can
run on supported data.

## Subsystem assessment

### 1. Environment and dependencies

Responsibility and entry points: `requirements.txt` pins the three direct runtime
dependencies; `README.md:3-19` defines Python 3.12 setup and the two test suites.
The manifest and imports are small and understandable. The ignored local `.venv`
has been moved from its creation path and its launcher is currently unusable, but a
fresh environment created by the documented command is the intended interface.

Target fit: the manifest is minimal, but current verification does not yet prove the
declared pins (TF-011). P40 adds no new third-party dependency. P40B documentation
should distinguish the clean supported environment from the bundled-runtime command
used for this audit and should include `python --version` and `pip check` in the
verification runbook. No dependency or external-API contract change is proposed.

### 2. Canonical data and schemas

Responsibility and entry points: `data_pipeline/contracts.py:112-218` owns the
canonical schema catalog and `validate_csv`; historical canonicalization enters via
`data_pipeline/historical_data/canonicalize.py`; the durable partitions are
`data/{rates,futures,market,contract_risk}`.

Target fit: the directory layout and validation boundary match. Material gaps are
the already-recorded P35 maturity inputs (TF-009) and the fact that P40 report files
do not implement their catalog schemas (TF-003). A direct header comparison under
Python 3.12.13 returned `False` for decisions, orders, fills, trades, and positions;
`summary.csv` is also structurally different. No canonical data file should be
fabricated during P40B.

### 3. Strategy and signal logic

Responsibility and entry points: `strategy/spread.py` implements approved equations;
`strategy/signal_generation.py` produces causal decisions; `strategy/portfolio.py`
ranks maturity targets. Phase 4 unit and golden tests cover signs, units, state
transitions, causal windows, risk precedence, and deterministic ranking.

Target fit: the pure core matches. The current backtest accepts any callback at
`backtesting/engine.py:198-202`; its tests use local scripted strategies rather than
a canonical complete-strategy adapter. That is valid as a mechanics harness but not
as evidence that the complete strategy ran. TF-004 requires the claim/label policy
to be made explicit. Broad strategy refactoring is not proposed.

### 4. Sizing and risk

Responsibility and entry points: the shared research path uses
`strategy/position_sizing.py` and `strategy/risk_signals.py`; Agent 0 independently
loads its historical size cap in `agents/agent_0/sizing.py` before its random policy
builds a plan.

Target fit: shared research functions are pure and fail closed in their focused
tests. Agent 0's current one-contract missing-data fallback violates that safety
direction and can reach submission (TF-001). The current broker-order allocation
also lost its stable contract-ID tie-break (TF-006).

### 5. Costs, accounting, and naive backtesting

Responsibility and entry points: `strategy/costs.py` owns pure cost calculations;
`backtesting/assumptions.py` names fixed naive assumptions;
`backtesting/engine.py:198-497` replays events, fills orders, accounts, and returns
records; `backtesting/reports.py:45-103` writes reports.

Target fit: timing, fixed costs, financing, reversals, rolls, partial fills, and the
five-day synthetic example are covered and reconcile. Two high issues remain:
missing held-leg inputs can publish partial economic equity (TF-002), and all report
schemas except daily are incompatible with the approved catalog (TF-003). The
writer's per-file temporary replacement is sufficient for the current single-writer
mechanics harness; no directory-staging framework or premature accounting-module
split is proposed.

### 6. Paper-agent architecture and safety boundaries

Responsibility and entry points: `agents/agent_0/run.py` builds and reconciles a
weekly random plan; `broker.py` enforces paper port/account checks and isolates IBKR;
`orders.py` persists local reconciliation state. `run.py --cancel-all` is explicitly
session-wide and documented.

Target fit: paper-only routing, managed-account checks, fake-broker tests, margin
preview, capacity checks, and network guards remain present. TF-001 can nonetheless
create risk without sizing evidence. TF-005 records an account identifier in a
tracked operational example and must be redacted. No external broker call was made
during this audit.

### 7. Tests and reproducibility

Responsibility and entry points: `docs/tests/` covers data, schemas, strategy, and
backtesting; `agents/agent_0/tests/test_characterization.py` covers broker behavior
with network guards and fakes; root self-checks retain the legacy path.

Target fit: coverage is broad and fast. Missing regression coverage is specific,
not systemic: missing Agent 0 sizing evidence (TF-001), two-leg incomplete marks
(TF-002), generated-report validation against the catalog (TF-003), and reversed
broker contract ordering (TF-006). `git diff --check` hygiene is TF-008. No new test
framework or dependency is warranted.

### 8. Documentation and onboarding

Responsibility and entry points: `README.md` covers basic setup and the legacy
layout; master-plan contracts are authoritative; phase evidence is under
`docs/verification/`.

At P40A, `docs/TECHNICAL_DOCUMENTATION.md` was absent and README did not map the
new backtesting path (TF-010). P40B now provides and links the onboarding entry
point. At P40B, a concrete paper account remained in Agent 0 settings (TF-005).
The later dirty-baseline cleanup redacted that value. The blanket `docs` ignore
rule still hides the two Phase 6 documents from normal Git discovery (TF-007).

## Ranked findings ledger

### TF-001 — Missing sizing evidence permits paper orders

- Severity: **high**
- Category: paper safety / sizing
- Exact evidence: `agents/agent_0/sizing.py:40-50,68-69,81-90` converts missing
  or unusable sizing evidence to zero, but `:101-105` changes every nonpositive
  cap to one. `agents/agent_0/run.py:226` passes those caps to
  `RandomPolicy.build_week_plan`; `random_policy.py:28-34,41-58` treats them as
  eligible; `run.py:233-235,171-185` then reaches broker submission.
  `PROJECT_CONTRACTS.md:607-608` requires stale/missing inputs to prevent new risk.
  The queue test patches `load_sizing_caps` at
  `agents/agent_0/tests/test_characterization.py:717` rather than testing failure.
- Impact: Agent 0 can generate and transmit one-contract paper orders without the
  approved historical sizing evidence. Margin preview is not a substitute for a
  strategy size cap.
- Smallest recommended action: delete only the fallback at `sizing.py:101-105`.
  Preserve zero caps so the existing `RandomPolicy` error occurs before `connect`.
- Validation: tests for missing/invalid sizing files, partial-valid input, and a
  fake assertion that `connect`/`placeOrder` are never called; then the Agent 0 suite.
- Proposed disposition: **approval-required structural change/deletion**.
- User disposition (2026-08-09): **ignore**; retain the fallback and make no
  TF-001 change in P40B.
- Consumers: `RandomPolicy.build_week_plan`, `run.queue_next_week`, Agent 0 settings.
- Risk: an Agent 0 run without valid sizing will stop instead of placing exploratory
  one-contract orders; this is the intended fail-closed effect.
- Recovery: restore the five deleted lines from Git if the user intentionally wants
  the fallback later.
- Verification required: focused missing-sizing regression and all Agent 0 tests.

### TF-002 — Missing held-leg inputs publish partial equity

- Severity: **high**
- Category: accounting correctness / material ambiguity
- Exact evidence: `backtesting/engine.py:249-268` adds held P&L leg by leg and only
  records missing legs, then `:398-426` publishes the remaining partial gross/net
  P&L and updates equity/drawdown. `:438-441` replaces prior marks, so the missing
  interval is not recovered when a mark returns. The current test at
  `docs/tests/test_naive_backtest.py:322-352` checks only `missing_input_count`.
  The basket identity in `PROJECT_CONTRACTS.md:50-56` requires every held leg.
- Impact: a two-leg basket can show plausible but economically incomplete P&L,
  equity, and drawdown while charging financing for all held legs.
- Smallest recommended action: fail the run before publishing a result whenever a
  held instrument lacks a required current/prior mark or usable multiplier.
- Validation: two-leg tests omitting one current mark, prior mark, or all usable
  multiplier evidence; each must fail before result publication. Re-run the golden
  accounting reconciliation unchanged.
- Proposed disposition: **approval-required structural change/deletion**.
- User disposition (2026-08-09): **option C**; retain partial accounting and add
  deterministic output evidence naming each missing date, instrument, and field.
- P40B result: implemented `missing_input_locations` in the manifest as a sorted,
  deterministic `date:instrument_id:field` list. Partial accounting is unchanged
  and is explicitly diagnostic rather than complete economic evidence.
- Clarification question: when any held leg cannot be valued, should the backtest
  (A) fail the run, (B) carry the last complete equity and emit an explicit
  unavailable daily row, or (C) retain current partial accounting?
- Plausible interpretations: A is simplest and fail closed; B preserves later replay
  but requires a new availability schema and gap-recovery policy; C treats missing
  legs as zero movement and is not recommended.
- Recommended interpretation: **A, fail the run**.
- Affected behavior: daily results, summary, manifest completion, missing-input
  diagnostics, and future realistic backtests.
- Consequence of deferral: synthetic complete-data tests remain usable, but no run
  containing missing held inputs may be treated as economic evidence.
- Consumers: report writer and all downstream result readers.
- Risk/recovery: failing earlier may reject previously accepted incomplete runs;
  revert the focused commit if the approved policy changes.
- Verification required: focused accounting tests, full P40 suite, golden identity.

### TF-003 — Six backtest reports violate approved schemas

- Severity: **high**
- Category: canonical schemas / accounting reports
- Exact evidence: `backtesting/reports.py:16-18,45-102` derives headers from runtime
  dataclasses and never calls `data_pipeline.contracts.validate_csv`. The catalog at
  `data_pipeline/contracts.py:182-216` disagrees with emitted decisions, orders,
  fills, trades, positions, and summary. Examples: `OrderIntent` fields at
  `strategy/models.py:368-383` do not include catalog `order_ref/created_at/status`;
  `FillRecord` at `backtesting/engine.py:93-108` does not match the catalog fill;
  `TradeRecord` at `:110-119` is appended once per nonzero fill at `:333-346`, while
  the catalog at `data_pipeline/contracts.py:200-204` requires an open/close trade
  lifecycle with gross/cost/net P&L. Only `daily.csv` matches. Existing P40 tests
  validate generated headers against their own dataclasses, not against `SCHEMAS`.
- Impact: artifacts claim `p40.backtest.v1` but cannot be consumed by the approved
  schema validator. `trades.csv` duplicates fill-like executions and cannot support
  trade-level reconciliation promised by its schema.
- Smallest recommended action: keep the approved schema meanings and add minimal
  report adapters/lifecycle accounting so every generated file validates. Do not
  version the catalog merely to bless accidental output shapes.
- Validation: an integration test must generate entry, partial fill, exit/reversal,
  and roll records; every CSV must pass `validate_csv`; trade gross minus costs must
  equal trade net and reconcile to fills/daily totals.
- Proposed disposition: **approval-required structural change/deletion**.
- User disposition (2026-08-09): **option A**; preserve the approved schema catalog
  and adapt P40 reports to it.
- P40B result: all seven non-manifest reports are mapped to the approved catalog and
  validated before replacement. Lifecycle trades now allocate gross P&L, financing,
  and fill costs by held instrument; direct, partial, rejected-open reversal, roll,
  close, and canonical ordering regressions pass. Decision `config_hash` fingerprints
  the declared configuration version plus effective mode, scope, window, and cost
  assumptions rather than hashing the version label alone.
- Clarification question: should P40B (A) preserve the approved schema catalog and
  adapt the reports, or (B) approve a new schema version whose artifacts are defined
  as execution traces rather than lifecycle accounting?
- Plausible interpretations: A preserves the MG3 contract but adds small lifecycle
  accounting; B is less implementation work but changes downstream meaning and
  leaves no trade-level P&L artifact.
- Recommended interpretation: **A, preserve the approved catalog**.
- Affected behavior: report dataclasses/adapters, report tests, schema catalog
  consumers, manifest schema version, and P40 evidence.
- Consequence of deferral: P40 outputs remain mechanics-only internal artifacts and
  must not be advertised as canonical backtest reports.
- Consumers: `write_results`, schema tests, future realistic backtest/report readers.
- Risk: mapping fields or lot lifecycle incorrectly can break accounting identities.
- Recovery: revert the isolated P40B schema-alignment commit; original files remain
  recoverable from Git.
- Verification required: catalog integration test, accounting specialist review,
  focused/full suites, and hand reconciliation.

### TF-004 — Complete-strategy and maturity-scope claims are ambiguous

- Severity: **high**
- Category: strategy contract / reproducibility claim
- Exact evidence: P35 says `complete_2y_5y` is reserved and not emitted because 2Y
  and 5Y remain unsupported (`docs/verification/P35.md:21,43-44,71-72`). P40
  hard-codes that scope at `backtesting/engine.py:462-467`, although
  `run_backtest` accepts any callable at `:198-202` and current tests use scripted
  callbacks. `docs/verification/P40.md:13-14,171-172` pairs the complete label with
  `synthetic_mechanics_only` and says it is not a historical result.
- Impact: a synthetic callback run can be mistaken for a supported complete-strategy
  2Y/5Y backtest even though no canonical strategy/data adapter ran.
- Smallest recommended action: retain the mechanics harness, but use an unambiguous
  synthetic-fixture scope/claim until P35 inputs and a concrete complete-strategy
  adapter exist. Keep the adapter/data work deferred rather than fabricating inputs.
- Validation: assert the approved label in manifest tests; reconcile P35/P40 evidence
  text; ensure no canonical-complete claim is made by a generic callback.
- Proposed disposition: **approval-required structural change/deletion**.
- User disposition (2026-08-09): **recommended action approved**; reserve
  `complete_2y_5y` and relabel the generic synthetic mechanics output.
- P40B result: the manifest emits `synthetic_fixture`; evidence retains
  `synthetic_mechanics_only`; `complete_2y_5y` remains reserved.
- Clarification question: did MG5/MG6 authorize `complete_2y_5y` as a synthetic-only
  mechanics label despite P35's explicit non-emission rule, or must the label change
  until 2Y/5Y pass P35 and the complete adapter exists?
- Plausible interpretations: retain the current label with evidence-class caveat, or
  reserve it for actual supported complete-strategy runs.
- Recommended interpretation: **reserve `complete_2y_5y`; relabel current outputs as
  synthetic mechanics only**.
- Affected behavior: manifest, P40 tests/evidence, future run naming and claims.
- Consequence of deferral: mechanics tests may continue, but current artifacts cannot
  be promoted as maturity-supported or complete-strategy evidence.
- Consumers/risk/recovery: report readers and phase evidence; changing labels can
  break literal assertions but not accounting. Revert the label-only commit if the
  user confirms the current interpretation.
- Verification required: focused manifest tests and documentation reference scan.

### TF-005 — A concrete paper account identifier appears in documentation

- Severity: **high**
- Category: security / documentation
- Exact evidence: `agents/agent_0/SETTINGS.md:67` contains a concrete DU-prefixed
  account instead of the prior placeholder. `PROJECT_CONTRACTS.md:667-668` forbids
  account identifiers in documentation.
- Impact: account-specific operational metadata can be committed or shared.
- Smallest recommended action: restore `YOUR_PAPER_ACCOUNT`; do not record the value
  in the audit or later evidence.
- Validation: repository scan for concrete DU-prefixed identifiers and review of the
  rendered command example.
- Proposed disposition: **safe cleanup**.
- User disposition (2026-08-09): **ignore**; make no TF-005 change in P40B.
  The later dirty-baseline cleanup restored `YOUR_PAPER_ACCOUNT`; this records a
  later state change without revising the P40B disposition.

### TF-006 — Contract allocation lost its deterministic tie-break

- Severity: **medium**
- Category: paper-agent reproducibility
- Exact evidence: the current change at `agents/agent_0/run.py:101-107` chooses the
  first minimum-capacity item and no longer uses contract ID as a secondary key.
  Tests at `agents/agent_0/tests/test_characterization.py:447-498` supply already
  ordered contract lists and do not reverse the input.
- Impact: identical broker state can assign different contract IDs when IBKR returns
  contract details in a different order.
- Smallest recommended action: restore the contract-ID secondary sort key and add a
  reversed-input regression test.
- Validation: compare allocations for forward/reversed contract inputs; Agent 0 suite.
- Proposed disposition: **safe cleanup**.
- User disposition (2026-08-09): **ignore**; make no TF-006 change in P40B.

### TF-007 — Blanket docs ignore hides required Phase 6 artifacts

- Severity: **medium**
- Category: repository configuration / maintainability
- Exact evidence: `.gitignore:15` ignores `docs`; `git check-ignore -v` confirms it
  hides both `docs/audits/technical-foundation-audit.md` and
  `docs/TECHNICAL_DOCUMENTATION.md`, while 75 existing docs files are tracked.
- Impact: required new audit/onboarding files do not appear in normal Git status or
  staging and can be omitted accidentally.
- Smallest recommended action: add narrow negation rules for only the audit folder
  and `docs/TECHNICAL_DOCUMENTATION.md`; keep unrelated ignored docs hidden.
- Validation: `git check-ignore` must report both required paths as not ignored;
  `git status` must show newly created required docs; existing ignored/cache behavior
  remains unchanged.
- Proposed disposition: **approval-required structural change/deletion**.
- User disposition (2026-08-09): **ignore**; make no TF-007 change in P40B.
- Consumers: Git workflows and future documentation authors.
- Risk: a broad unignore could expose unrelated local documents; narrow exceptions
  avoid that.
- Recovery: revert the `.gitignore` hunk and use explicit `git add -f` for the two
  files.
- Verification required: check-ignore/status tests and inspection of newly visible
  files.

### TF-008 — Current diff fails the repository whitespace gate

- Severity: **low**
- Category: repository hygiene
- Exact evidence: `git diff --check` reports a new blank line at
  `agents/agent_0/config.py:121` and trailing whitespace at
  `agents/agent_0/run.py:300`.
- Impact: MG6A's required diff check cannot pass.
- Smallest recommended action: remove only those whitespace errors.
- Validation: `git diff --check` exits zero apart from non-failing line-ending notices.
- Proposed disposition: **safe cleanup**.
- User disposition (2026-08-09): **ignore**; make no TF-008 change in P40B.

### TF-009 — Canonical maturity inputs remain incomplete

- Severity: **high**
- Category: canonical data / explicit upstream blocker
- Exact evidence: `docs/verification/P35.md:43-46,51-71` records no canonical
  maturity-matched swap rates, active contract reference/multipliers, paper
  liquidity, or integrated mapping for 2Y/5Y, and no usable 10Y/30Y set.
- Impact: no representative historical complete-strategy run or maturity-support
  claim can be produced safely.
- Smallest recommended action: retain the approved P35 blockers; do not create data,
  a contract map, or a historical run in P40B. Resolve through the dedicated future
  data/contract work before realistic backtesting.
- Validation: future P35-equivalent coverage and integrated golden mapping.
- Proposed disposition: **defer** (already retained when MG5 was approved).
- User disposition (2026-08-09): **ignore**; retain the existing deferral.

### TF-010 — The required onboarding entry point was absent

- Severity: **medium**
- Category: documentation/onboarding
- Exact P40A evidence: `docs/TECHNICAL_DOCUMENTATION.md` was absent;
  `README.md:21-33` described the legacy root pipeline but not the P40 backtesting
  package. `PROJECT_CONTRACTS.md:645-668` defines the required layered document.
- Impact: a capable new contributor cannot reproduce the current architecture and
  distinguish runnable mechanics from deferred historical/realistic work without
  reading phase history.
- Smallest recommended action: P40B creates the required quick-start-first document,
  links rather than restates authoritative equations/schemas, labels unavailable
  commands as planned, and includes verified offline commands and safety boundaries.
- Validation: execute every command presented as available; reference, secret,
  newcomer, and scope review.
- Proposed disposition: **documentation-only**.
- User disposition (2026-08-09): **ignore**; do not create
  `docs/TECHNICAL_DOCUMENTATION.md` in P40B.
- Superseding user authorization (2026-08-09): execute the full P40B prompt and
  create `docs/TECHNICAL_DOCUMENTATION.md` as the primary onboarding entry point.
- Final disposition: **implemented**. The new document uses quick-start, system,
  and technical-reference layers; links authoritative equations/schemas; separates
  runnable mechanics from deferred work; records paper-only operations and verified
  external facts; and is linked from `README.md`.
- Verification evidence: 10/10 local Markdown paths resolve and the new document
  contains zero concrete account literals. Final command and reviewer evidence is
  recorded in the P40B result below.

### TF-011 — Current verification did not use the declared dependency pins

- Severity: **medium**
- Category: environment / reproducibility
- Exact evidence: `requirements.txt:2-3` pins `numpy==2.3.5` and `pandas==3.0.1`.
  The offline audit runtime reported `numpy 2.5.0` and `pandas 3.0.3`; the current
  ignored `.venv` launcher fails because `pyvenv.cfg` points to a missing prior
  Microsoft Store interpreter location. The suites pass only when the bundled
  Python 3.12.13 executable is combined with those existing workspace packages.
- Impact: current results are useful compatibility evidence but do not prove that a
  fresh checkout installs and passes with the repository's approved manifest.
- Smallest recommended action: label the current results as bundled-runtime
  evidence, then during P40B create a fresh disposable Python 3.12 environment from
  `requirements.txt`, run `pip check`, and execute every documented supported
  command before claiming clean-environment reproducibility. Do not change pins
  merely to match this ignored local environment.
- Validation: record Python/package versions, successful `pip check`, focused/full
  suites, self-checks, and commands included in the onboarding document.
- Proposed disposition: **documentation-only**.
- User disposition (2026-08-09): **ignore**; do not perform TF-011 work in P40B.

## Proposed target tree differences

Only one new path is justified now:

```text
docs/
  TECHNICAL_DOCUMENTATION.md   # P40B onboarding/runbook
  audits/
    technical-foundation-audit.md
```

Do not create `backtesting/accounting.py` or `agents/shared/` during P40B merely to
match the aspirational tree. Add either only when an approved later phase provides a
second consumer or a demonstrated boundary problem. Do not delete the legacy root
pipeline while README, import smoke, self-checks, and historical consumers still use
it.

## Exact deletion and structural-rewrite approval list

No file deletion is proposed.

The user recorded these MG6A dispositions on 2026-08-09:

1. **TF-001:** ignore; retain the one-contract missing-sizing fallback.
2. **TF-002:** option C; retain partial accounting and report exact missing locations.
3. **TF-003:** option A; preserve the approved schemas and adapt output.
4. **TF-004:** apply the recommended synthetic-only relabeling.
5. **TF-007:** ignore; retain the blanket docs ignore rule.

TF-005, TF-006, TF-008, and TF-011 were also explicitly ignored. TF-009 retains
its existing deferral. The later full-P40B instruction superseded only TF-010,
authorizing the onboarding entry point in addition to TF-002, TF-003, and TF-004.
The subsequent dirty-baseline cleanup resolved the TF-005 account-literal state;
the other recorded dispositions remain unchanged.

## Review record

One fresh lower-tier read-only technical reviewer checked evidence quality,
requirement coverage, scope discipline, and unnecessary proposals. It independently
confirmed TF-001, TF-002, TF-004, TF-005, TF-006, and TF-007 and recommended no
directory-atomic writer rewrite or speculative module extraction.

Because concrete findings involved broker safety and accounting, one additional
lower-tier specialist review was used, limited to those domains. It confirmed the
direct missing-sizing-to-`placeOrder` path, the partial held-leg P&L and lost recovery
interval, and TF-003's schema/accounting mismatch. Evidence errors were corrected;
the final technical re-check returned PASS. No broader specialist or external-API
review was needed.

The fresh final P40B consolidated review found that the retained `docs` ignore rule
would omit both required deliverables from a normal commit and that the onboarding
guide needed to name its then-accepted TF-005 contradiction beside the Agent 0
settings link. The two exact deliverables were force-staged without changing
`.gitignore`, and the guide now warns readers not to copy or extend that
account-literal exception. The later dirty-baseline cleanup redacted the setting.
A cached diff check then exposed two trailing Markdown spaces in this new audit
file; they were removed. The fresh accounting/schema/broker-safety specialist
review returned PASS.

## P40B result and MG6A status

The authorized P40B work is implemented. Focused P40 tests pass 16/16; the full
documentation suite passes 296/296 and Agent 0 passes 25/25 when the bundled Python
3.12.13 runtime uses the existing ignored `.venv` site-packages. Compilation and all
three legacy self-checks pass. Both lower-tier re-reviewers returned PASS after the
trade-ordering, phantom-reversal, partial-reversal attribution, and configuration
fingerprint regressions were added.

The full documentation command set also passes: 14/14 schema tests, `pip check`,
10/10 local onboarding links, and a zero-account-literal scan of the new guide.
The clean-install claim remains excluded because TF-011 was not authorized.

The user approved MG6A on 2026-08-09 after reviewing the implemented work and
evidence, then explicitly authorized the previously omitted onboarding document.
TF-010 is now implemented. The sign-off retains three recorded exceptions: TF-007
keeps this audit and onboarding document ignored by normal Git discovery; TF-008
keeps the pre-existing diff-check errors; and TF-011 keeps the broken `.venv`
launcher/fresh-environment verification gap. The approval does not convert those
exceptions into passed criteria or authorize unlisted work.
