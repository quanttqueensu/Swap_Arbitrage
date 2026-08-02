# P24 Task 1 report: source-neutral provenance contracts

## Evidence

- Added `SchemaCatalogTests.test_historical_contracts_route_only_approved_sources`
  with literal source-neutral rate and futures destinations and a guard against
  `quantt` in any migration destination.
- The requested `python -m unittest ...` command could not start because
  `python` is absent from `PATH`; the repository-main `.venv\\Scripts\\python.exe`
  also points to a missing Windows Store interpreter. The observed error was
  `No Python at '...PythonSoftwareFoundation.Python.3.12...\\python.exe'`.
- Used the available bundled Python 3.12.13 at
  `docs/.worktrees/p01/.venv/Scripts/python.exe` to run the focused test:
  `test_historical_contracts_route_only_approved_sources ... ok`.
- Ran `python -m unittest tests.test_schema_contracts -v` with that same
  interpreter: `Ran 16 tests in 0.127s`, `OK`. This includes the exact
  1,487-artifact migration-coverage test.
- Ran `git diff --check`: exit code 0.

## Self-review

- `RATE_SOURCE_PATH` and `FUTURES_SOURCE_PATH` are immediately above `SCHEMAS`.
  They supply both historical schema paths and every affected migration-rule
  destination.
- The historical schemas retain their columns, keys, ordering, frequency,
  retention, and validation-rule tuples; only path and first consumer changed
  to `data_pipeline.rates_source` and `data_pipeline.futures_source`.
- Active contracts and migration destinations are provider-neutral while row
  `source` remains part of both historical schema headers and keys.
- `r2_objects.csv` remains in place and is explicitly excluded from canonical
  manifests.
- No migration action was performed and no historical verification record was
  edited.

## Concern

The primary worktree has no runnable `python` command and its main virtual
environment references a removed interpreter. Verification therefore used the
available bundled P01 interpreter; the contract tests use only the standard
library.

## Fix round 1 evidence

- Corrected the prior overclaim about remaining provider-specific text. Active
  `PROMPT_PLAYBOOK.md` and `VERIFICATION_GATES.md` directives were still
  provider-specific; this round retires P22, advances P23/P24 without it, and
  replaces active primary directives with approved local, IBKR, FRED, and CME
  adapter language.
- Expanded `test_historical_contracts_route_only_approved_sources` to assert
  exact first consumers, every affected migration destination, no Quantt or
  Cloudflare destination, `source` in both historical headers and keys, and
  the in-place/excluded `r2_inventory` destination.
- RED was observed by evaluating these literal routing invariants against
  `HEAD^:data_pipeline/contracts.py`; it failed with `AssertionError` on the
  former provider-specific historical-rate path. The focused expanded test
  then passed against the current contract revision.

## Fix round 1 self-review

- P22 is explicitly retired; it neither authorizes nor blocks an external
  provider ingestion path. P23 follows MG3 and P24 follows P23 fake-broker
  coverage plus MG4 source-neutral evidence.
- MG4 now verifies source-neutral paths, schema headers/keys, and row-level
  `source` provenance for approved local, IBKR, FRED, and CME adapters.
- Historical baseline and approval records remain untouched. Provider names
  remain only where they label retired or historical material, not active
  source-routing directives.

## Fix round 2 evidence and self-review

- Removed retired P22 from the active Phase 3 execution list; it now lists
  P23 and P24 only.
- Corrected P24 sequencing: P24 follows P23 fake-broker schema coverage and
  creates staged/dry-run source-neutral migration evidence for MG4 review.
  MG4, rather than a prerequisite to P24, gates publication, strategy
  consumption, and P30.
- No text regression was added because these human-facing planning directives
  have no executable consumer; source-text tests would only detect an
  intentional wording change rather than exercise behavior.
- Historical verification records were not edited.

## Fix round 3 evidence and self-review

- Corrected P23's active sequencing: it now completes fake-broker coverage and
  evidence, then proceeds directly to P24. P24, not P23, produces the staged
  dry-run evidence that is reviewed at MG4.
- Historical verification records were not edited.
