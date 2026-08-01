# P21 Canonical Schemas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze reusable narrow CSV contracts and a complete, non-destructive migration preview for all 1,487 P20-audited artifacts.

**Architecture:** A standard-library `data_pipeline.contracts` module owns immutable schema metadata and pure fixture validation. Human-readable schema and migration documents explain the same contracts, while tests prove metadata completeness, representative validation behavior, and exact one-rule migration coverage without changing real data.

**Tech Stack:** Python 3.12 standard library (`csv`, `dataclasses`, `datetime`, `decimal`, `pathlib`, `re`), `unittest`, and Markdown.

## Global Constraints

- Execute only P21 on the clean linked worktree branch based on committed P20 evidence.
- Use schema version `1.0.0` for the initial freeze.
- Do not contact Cloudflare/R2, IBKR, or any network endpoint.
- Do not move, rewrite, archive, supersede, delete, or stage real data.
- Preserve the separate dirty main checkout unchanged.
- Every audited P20 artifact must match exactly one migration-preview rule.
- Raw Eris vendor caches remain immutable and have no automatic deletion date.
- A schema field exists only for a named approved strategy, risk, execution, accounting, data-quality, or audit consumer.
- Stop at MG3; approval permits later staging only, never deletion.

---

### Task 1: Executable schema records and RED tests

**Files:**
- Create: `data_pipeline/__init__.py`
- Create: `data_pipeline/contracts.py`
- Create: `tests/test_schema_contracts.py`

**Interfaces:**
- Produces: `ColumnContract`, `CsvContract`, `SCHEMAS`, `SchemaValidationError`, and `validate_csv(contract, path)`.

- [ ] **Step 1: Write failing imports and contract-completeness tests**

Create `tests/test_schema_contracts.py` importing the five public names above.
Assert that each contract has version `1.0.0`, a path pattern, columns with
nonempty name/type/unit, a nonempty unique key and ordering, update frequency,
retention, consumers, and validation rules. Require the schema IDs named in
`PROJECT_CONTRACTS.md`, plus separate backtest trade/position/summary outputs.

- [ ] **Step 2: Run RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_schema_contracts -v`

Expected: `ModuleNotFoundError: No module named 'data_pipeline'`.

- [ ] **Step 3: Add immutable records and the complete catalog**

Implement frozen dataclasses:

```python
@dataclass(frozen=True)
class ColumnContract:
    name: str
    scalar_type: str
    unit: str
    nullable: bool = False

@dataclass(frozen=True)
class CsvContract:
    schema_id: str
    version: str
    path_pattern: str
    columns: tuple[ColumnContract, ...]
    unique_key: tuple[str, ...]
    ordering: tuple[str, ...]
    missing_value_policy: str
    update_frequency: str
    retention: str
    consumers: tuple[str, ...]
    validation_rules: tuple[str, ...]
```

Populate `SCHEMAS` from the project contract. Add `available_at_utc`,
`classification`, and nullable `proxy_label` only to canonical market input,
because the approved P10 clock and proxy lineage consume them.

- [ ] **Step 4: Run completeness GREEN**

Run the focused module and expect all catalog tests to pass.

### Task 2: Fixture validation behavior

**Files:**
- Modify: `data_pipeline/contracts.py`
- Modify: `tests/test_schema_contracts.py`

**Interfaces:**
- Consumes: Task 1 contract records.
- Produces: deterministic row validation with no file writes.

- [ ] **Step 1: Add failing temporary-fixture tests**

Use `TemporaryDirectory` and `csv.DictWriter` to cover a valid daily-market
fixture and failures for wrong header, missing required value, wrong scalar
type, duplicate unique key, unsorted rows, non-UTC timestamp, both/neither
market identities, publication before observation violation, nonpositive
prices/sizes where required, and crossed quote.

- [ ] **Step 2: Run RED**

Expected: imports succeed but validation tests fail because `validate_csv` is
not implemented.

- [ ] **Step 3: Implement minimal generic validation**

Parse exact headers and scalar types, compare normalized key/order tuples,
then apply only named row rules (`one_market_identity`, `utc_timestamp`,
`available_not_before_observation`, `positive_quote_fields`, `bid_not_above_ask`).
Raise `SchemaValidationError` with row number and rule evidence.

- [ ] **Step 4: Run focused GREEN**

Run the focused module and expect all validation tests to pass.

### Task 3: Freeze the human-readable canonical schemas

**Files:**
- Create: `docs/data/canonical-schemas.md`
- Modify: `tests/test_schema_contracts.py`

**Interfaces:**
- Consumes: `SCHEMAS` and the approved equations/contracts.
- Produces: the MG3 schema review document and catalog/document drift tests.

- [ ] **Step 1: Add failing documentation coverage tests**

For every contract, require the document to contain its schema ID, version,
path, exact ordered header, key, ordering, missing policy, frequency,
retention, consumers, and one sample row. Also require explicit units/types
for every column and the no-DV01-duplication rule.

- [ ] **Step 2: Run RED**

Expected: failure because `docs/data/canonical-schemas.md` is absent.

- [ ] **Step 3: Write the schema document**

Explain global encoding/partition/version rules, then one section per catalog
entry with every frozen attribute and a representative synthetic row. Record
the P10 causal availability fields and fail-closed proxy semantics. Mark
10Y/30Y and exact unavailable sources as unsupported rather than fabricating
rows.

- [ ] **Step 4: Run documentation GREEN**

Run the focused module and expect catalog/document coverage to pass.

### Task 4: Complete migration preview and coverage proof

**Files:**
- Create: `docs/data/migration-preview.md`
- Modify: `tests/test_schema_contracts.py`

**Interfaces:**
- Consumes: `docs/data/current-inventory.md` artifact headings.
- Produces: exact one-rule classification of all 1,487 paths.

- [ ] **Step 1: Add failing preview tests**

Parse every P20 artifact heading. Encode non-overlapping matchers for the Eris
cache family and 13 named current artifacts. Assert 1,487 inputs, exactly one
match each, action membership in `keep immutable source`, `regenerate`,
`archive labelled legacy`, or `supersede after validation`, and zero present-
tense destructive commands.

- [ ] **Step 2: Run RED**

Expected: failure because `docs/data/migration-preview.md` is absent.

- [ ] **Step 3: Write the preview**

For each rule record matched count, current rows/columns, action, named staged
destination, expected row/column relationship, prerequisites, recovery, and
retention. State that every action is future and no action was performed.

- [ ] **Step 4: Run preview GREEN**

Run the focused module and expect all 1,487 artifacts to have exact coverage.

### Task 5: Verification record and manual gate

**Files:**
- Create: `docs/verification/P21.md`

- [ ] **Step 1: Run focused and full verification**

Run schema tests, full unittest discovery, `compileall`, all four existing
`--self-check` commands, `git diff --check`, and `git status --short`. Record
exit codes and test counts.

- [ ] **Step 2: Perform fresh read-only reviews**

Run separate requirements, schema keys/units, quality, and migration-safety
passes. Resolve every actionable finding with another RED/GREEN cycle.

- [ ] **Step 3: Write and self-check the verification record**

Record files, TDD evidence, exact commands/outcomes, reviews, external-system
count zero, data-change count zero, limitations, MG3 request, paper-only
assertion, and broker-order submission count zero.

- [ ] **Step 4: Run requested Ponytail audit**

Audit the whole maintained repository for over-engineering, report ranked
findings only, and do not apply them.

- [ ] **Step 5: Commit P21 and stop**

Stage only P21 files, commit, and stop at MG3 without beginning P22.
