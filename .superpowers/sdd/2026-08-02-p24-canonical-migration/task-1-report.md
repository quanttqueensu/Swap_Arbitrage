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
  manifests. The only remaining `Quantt`/`Cloudflare` text is preserved in
  labelled historical baseline records in `MASTER_PLAN.md`.
- No migration action was performed and no historical verification record was
  edited.

## Concern

The primary worktree has no runnable `python` command and its main virtual
environment references a removed interpreter. Verification therefore used the
available bundled P01 interpreter; the contract tests use only the standard
library.
