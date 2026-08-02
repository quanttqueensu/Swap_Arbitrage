# P24 Canonical Migration Design

## Scope

P24 converts the already-approved local FRED/CME-derived artifacts into the P21
canonical contracts, writes provenance manifests, proves the result in an
isolated repository-local staging tree, and then publishes only validated
recoverable canonical files. It makes no network request and adds no
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
determinism, and writes `migration_report.csv`. Publication copies validated
files through temporary siblings and atomic replacement; it never deletes,
moves, or rewrites an original.

## Inputs and Outputs

The first supported migration consumes only:

- `data/cme_swap_data.csv`;
- `data/treasury_futures_data.csv`;
- `data/treasury_rates.csv`;
- the approved 2Y/5Y fields required from `data/swap_rates.csv` and
  `data/treasury_futures.csv`;
- approved Eris cache rows when they are needed to verify source-level
  settlement lineage.

It writes:

- `data/source/rates/rates_YYYY.csv`;
- `data/source/futures/futures_settlements_YYYY.csv`;
- `data/canonical/reference/contract_risk_YYYY.csv`;
- `data/canonical/market/daily_market_YYYY.csv`;
- `data/manifests/p24_inputs.csv`;
- `data/manifests/p24_run.csv`;
- `docs/verification/P24-migration-report.csv` in the staged/report output.

The R2 inventory remains at `r2_objects.csv` as immutable historical metadata
and is excluded from every canonical input manifest.

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
availability timestamps use the source timing frozen by MG2/P21. A row with
unknown timing, unit, identity, or classification blocks the partition.

## Staging and Reconciliation

Each migration rule records its original path, staged destination, action,
source hash, staged hash, source row count, staged row count, start/end dates,
schema version, validation status, and recovery path. Required reconciliation
is rule-specific:

- source-to-output key sets match for every emitted approved field;
- yearly totals sum to the expected source totals;
- duplicate and unsorted keys block publication;
- literal first/middle/last spot checks compare dates, identities, prices,
  rates, DV01, units, and proxy labels;
- two independent staging runs produce identical relative paths and bytes.

The publish step is enabled only after all report rows are `pass`. Existing
canonical files are replaced atomically, so interruption leaves either the old
or new complete file. Original wide files, caches, and legacy backtests are not
modified or archived in P24.

## Error Handling and Safety

The command accepts explicit `--repo-root`, `--staging-root`, and `--report`
paths. Symlinks and resolved paths outside the repository are rejected. A
nonempty staging directory is rejected rather than cleared. Temporary files
are confined to destination siblings. On any mismatch the command exits
nonzero, retains the staging evidence, and performs no publication.

## Testing

Unit tests use small literal CSV fixtures for every transform. Integration tests
build a temporary repository containing representative source files and prove
deterministic staging, exact reconciliation, manifests, atomic publication,
failure preservation, path containment, proxy labeling, and source immutability.
A test monkeypatches socket operations before importing the migration modules to
prove that the complete P24 path is offline.

## Deliverables

- `data_pipeline/canonicalize.py`
- `data_pipeline/manifests.py`
- `data_pipeline/migration.py`
- `tests/test_canonical_migration.py`
- `docs/verification/P24.md`
- staged and published canonical data plus manifests after all checks pass
