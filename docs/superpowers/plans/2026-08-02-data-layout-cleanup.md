# Data Layout Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or `superpowers:executing-plans`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize durable data under five named folders and remove manifest/staging functionality without changing any data bytes.

**Architecture:** Raw and legacy CSVs live under `data/raw_data`; canonical partitions live directly under `data/futures`, `data/rates`, `data/market`, and `data/contract_risk`. Pure canonicalizers and schema validators remain; manifest generation, migration staging/publication, persisted manifests, reports, and their tests are removed.

**Tech Stack:** Python 3.12 standard library, existing CSV schema validators, PowerShell/Git moves, `unittest`.

## Global Constraints

- Preserve every retained CSV byte and SHA-256 value while moving files.
- Do not retain `data/manifests` or `data/staging` in the durable tree.
- Do not change canonical row values, schema columns, source labels, or strategy behavior.
- Keep pure canonicalization and schema validation covered by tests.
- Run the full suite and `git diff --check` before committing.

---

### Task 1: Lock the new durable path contract

**Files:**
- Modify: `config.py`
- Modify: `data_pipeline/contracts.py`
- Modify: `tools/data_audit.py`
- Test: `tests/test_schema_contracts.py`
- Test: `tests/test_dv01_pipeline.py`

- [ ] Add failing assertions for raw inputs under `data/raw_data/` and canonical paths under the four named output folders.
- [ ] Update path constants, schema path patterns, migration rules that remain relevant to raw/canonical classification, and audit rules.
- [ ] Run focused tests and confirm the old paths fail before implementation.
- [ ] Run focused tests again and confirm the new path contract passes.

### Task 2: Move bytes into the five durable folders

**Files:**
- Move: top-level `data/*.csv` raw inputs and legacy backtests to `data/raw_data/`
- Move: `data/source/futures/*.csv` to `data/futures/`
- Move: `data/source/rates/*.csv` to `data/rates/`
- Move: `data/canonical/market/*.csv` to `data/market/`
- Move: `data/canonical/reference/contract_risk_*.csv` to `data/contract_risk/`

- [ ] Record pre-move hashes for every retained CSV.
- [ ] Move each exact file without rewriting bytes.
- [ ] Verify destination hashes and assert the old durable paths no longer exist.
- [ ] Remove empty obsolete `source`, `canonical`, `manifests`, and `staging` directories.

### Task 3: Remove manifest/staging subsystem

**Files:**
- Delete: `data_pipeline/manifests.py`
- Delete: `data_pipeline/migration.py`
- Delete: `tests/test_canonical_migration.py`
- Create: `tests/test_canonicalize.py` containing the retained pure canonicalizer tests
- Delete: `data/manifests/*`, `data/staging/*`, `docs/verification/P24.md`, `docs/verification/P24-migration-report*.csv`

- [ ] Move the canonicalizer test class into a focused test module and remove all manifest/staging/publication tests.
- [ ] Remove imports and documentation references to the deleted modules and artifacts.
- [ ] Run import smoke and focused canonicalizer tests.

### Task 4: Update readers and documentation

**Files:**
- Modify: `raw_price_data.py`, `risk_data.py`, `backtest.py`, `docs/data/*`, `docs/master-plan/*`, `README.md`
- Modify: any remaining path references found by `rg`

- [ ] Update every raw-data reader to use `data/raw_data`.
- [ ] Update every canonical-data reader and contract description to use the four output folders.
- [ ] Remove P24 manifest/staging execution instructions while retaining source/provenance and MG4 context.
- [ ] Run a repository-wide reference scan showing no active manifest/staging path or deleted module import remains.

### Task 5: Verify and commit

- [ ] Run schema, canonicalizer, data-audit, DV01, import-smoke, and full test suites.
- [ ] Run `git diff --check`.
- [ ] Verify `data/` contains only `raw_data`, `futures`, `rates`, `market`, and `contract_risk` (plus no deleted manifest/staging folders).
- [ ] Commit the cleanup on `main`.
