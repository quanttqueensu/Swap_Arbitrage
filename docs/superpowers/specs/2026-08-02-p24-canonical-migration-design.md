# P24 Canonical Migration Design

## Scope

P24 converts the already-approved local FRED/CME-derived artifacts into the P21
canonical contracts, writes provenance manifests, proves the result in an
isolated repository-local staging tree, and produces a deterministic migration
report. Task 4 does not publish canonical files. It makes no network request and adds no
Quantt/Cloudflare dependency. Source caches and legacy results remain in place.

## Architecture

`data_pipeline/canonicalize.py` contains pure, source-specific row transforms.
It converts `treasury_rates.csv` into yearly rate partitions while preserving
U.S. Treasury and New York Fed provenance; converts the approved Eris and
Treasury-futures masters into yearly settlement and
contract-risk partitions; and converts approved long market observations into
yearly `daily_market` partitions. Every transform returns rows in contract
ordering and carries an explicit source, classification, proxy label,
observation time, and availability time.

`data_pipeline/manifests.py` profiles an output only after schema validation and
produces SHA-256, row count, date/time coverage, and schema version. It also
hashes the complete ordered input-manifest rows so a migration run has one
stable identity.

`data_pipeline/migration.py` orchestrates a fail-closed run. It resolves all
inputs and outputs beneath an explicit repository root, builds a fresh staging
tree, invokes the pure transforms, validates every file, compares source and
output keys/counts/spot values, reruns into a second staging tree to prove byte
determinism, and writes `migration_report.csv`. Publication remains disabled
and reserved for Task 5; Task 4 never deletes, moves, or rewrites an original.

## Inputs and Outputs

The first supported migration consumes exactly these five catalogued inputs:

- `data/cme_swap_data.csv`;
- `data/treasury_futures_data.csv`;
- `data/treasury_rates.csv`;
- the approved 2Y/5Y fields required from `data/swap_rates.csv`;
- the approved 2Y/5Y fields required from `data/treasury_futures.csv`.

Task 4 stages only:

- `data/source/rates/rates_YYYY.csv`;
- `data/source/futures/futures_settlements_YYYY.csv`;
- `data/canonical/reference/contract_risk_YYYY.csv`;
- `data/canonical/market/daily_market_YYYY.csv`;
- `data/manifests/p24_inputs.csv`;
- `docs/verification/P24-migration-report.csv` in the staged/report output.

Task 4 does not create `data/manifests/p24_run.csv`: before publication and
final evidence there is no honest immutable code commit for the code that
performed publication, publication start/end time, or terminal status to record. Task 5 creates that file only after
publication and evidence are complete, validates it against the registered
`run_manifest` contract, and records the real immutable code commit that
exactly matches the executed publication code, the exact
`p24_inputs.csv` manifest digest, true publication timestamps/status, and
deterministically serialized run ID, configuration, strategy, and row-count
metadata.

The other 1,482 catalogued artifacts are excluded. They include all 1,474 Eris
vendor-cache files and the top-level `r2_objects.csv` inventory, which remains
immutable historical metadata and is excluded from every canonical input
manifest. Exact catalog coverage remains 1,487 artifacts: five consumed plus
1,482 excluded.

## Conversion Rules

Rates retain their true provider and series identity and are converted to basis
points without forward filling. Futures settlement prices retain price-point
units and their true provider or proxy provenance. For this migration, DV01 is
written only to `contract_risk`; the nullable settlement DV01 column remains
blank so the canonical no-duplication rule is satisfied.

The current Treasury continuous-root and fixed-ratio values remain `proxy`,
with nonempty proxy labels. U.S. Treasury, New York Fed, Eris, and Yahoo-derived
rows are never relabelled as FRED or CME observations. No missing exact input is
replaced with a proxy or zero. Dates are normalized to `YYYY-MM-DD`;
availability timestamps use a conservative P24 assumed timing matrix pending
MG4 approval; P21 does not prove source availability. A row with
unknown timing, unit, identity, or classification blocks the partition.

The Task 4 fixture-stage timing matrix is explicitly effective dated from
`2000-01-01` through `2099-12-31`: ERIS and Yahoo observations at `21:00:00Z`
are assumed available one minute later. The matrix has a stable rule ID and
digest, and its timing certainty is `assumed`. ERIS rows retain economic
classification `exact`; Yahoo rows retain economic classification `proxy` and
a nonempty proxy label. MG4 approval remains required, and an uncovered date
blocks rather than being inferred.

## Staging and Reconciliation

Each migration rule records its original path, staged destination, action,
source hash, staged hash, source row count, staged row count, start/end dates,
schema version, validation status/detail, recovery path, exact key/value evidence,
and literal first/middle/last spot evidence. Required reconciliation
is rule-specific:

- source-to-output key sets match for every emitted approved field;
- yearly totals sum to the expected source totals;
- duplicate and unsorted keys block publication;
- literal first/middle/last spot checks compare dates, identities, prices,
  rates, DV01, units, and proxy labels;
- two independent staging runs produce identical relative paths and bytes.

Task 4 consumes exactly five source inputs (`treasury_rates`, `cme_swap_master`,
`treasury_futures_master`, `swap_rates`, and `treasury_futures`). Eris vendor
cache, raw-wide, signal/risk-wide, legacy backtests, and R2 inventory remain
catalogued exclusions in this stage. The exclusions total 1,482 artifacts,
including 1,474 Eris cache files and the R2 inventory; no report row implies
they were migrated.

Publication is disabled in Task 4 even when all report rows are `pass`.
Original wide files, caches, and legacy backtests are not modified or archived
in this task.

## Error Handling and Safety

The command accepts explicit `--repo-root`, `--staging-root`, and `--report`
paths. Reports are confined to `docs/verification` and may not overlap data,
staging, manifests, or canonical destinations. Symlinks and resolved paths
outside the repository are rejected. A nonempty staging directory is rejected
rather than cleared. On failure, only a staging tree created by that invocation
is removed so the same target is retryable; pre-existing paths are never removed.
Temporary files are confined to staging. The command performs a second private
shadow stage and compares path-independent bytes before returning, then removes
the shadow evidence. No publication occurs.

## Testing

Unit tests use small literal CSV fixtures for every transform. Integration tests
build a temporary repository containing representative source files and prove
deterministic staging, exact reconciliation, manifests, failure preservation,
path containment, proxy labeling, and source immutability.
A test monkeypatches socket operations before importing the migration modules to
prove that the complete P24 path is offline.

## Deliverables

- `data_pipeline/canonicalize.py`
- `data_pipeline/manifests.py`
- `data_pipeline/migration.py`
- `tests/test_canonical_migration.py`
- `docs/verification/P24.md`
- staged canonical data plus manifests and a migration report; no publication
