# P24 Canonical Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert approved existing rate and futures artifacts into validated, provenance-rich canonical partitions with deterministic staging, manifests, and recoverable atomic publication.

**Architecture:** Pure canonicalizers produce contract-shaped rows with true provider lineage. A manifest module hashes validated outputs, while a migration orchestrator stages, reconciles, reruns for byte determinism, reports, and publishes only after every validation passes.

**Tech Stack:** Python 3.12 standard library (`argparse`, `csv`, `datetime`, `decimal`, `hashlib`, `pathlib`, `shutil`, `tempfile`), existing schema catalog, and `unittest` fixtures.

## Global Constraints

- P24 is fully offline and never imports an HTTP, Cloudflare, broker, or market-data client.
- Preserve true U.S. Treasury, New York Fed, Eris, Yahoo-proxy, FRED, CME, and IBKR provenance; never relabel one provider as another.
- No missing exact value becomes zero or proxy; no forward fill crosses a gap or roll.
- Inputs, caches, R2 inventory, and legacy backtests remain byte-identical.
- Publication uses validated temporary siblings and atomic replacement only.
- All resolved paths stay inside the explicit repository root; symlinks fail closed.

---

### Task 1: Correct source-neutral contracts and migration destinations

**Files:**
- Modify: `data_pipeline/contracts.py`
- Modify: `tests/test_schema_contracts.py`
- Modify: `docs/data/canonical-schemas.md`
- Modify: `docs/data/migration-preview.md`
- Modify: `docs/master-plan/MASTER_PLAN.md`
- Modify: `docs/master-plan/PROJECT_CONTRACTS.md`

**Interfaces:**
- Consumes: existing `historical_rates` and `historical_futures_settlements` schemas.
- Produces: source-neutral paths `data/source/rates/rates_YYYY.csv` and `data/source/futures/futures_settlements_YYYY.csv`, with provider identity retained in each row's `source` field.

- [ ] **Step 1: Change the existing source-routing test to the source-neutral contract**

```python
self.assertEqual(rates.path_pattern, "data/source/rates/rates_YYYY.csv")
self.assertEqual(settlements.path_pattern, "data/source/futures/futures_settlements_YYYY.csv")
self.assertFalse(any("quantt" in rule.destination.lower() for rule in MIGRATION_RULES))
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m unittest tests.test_schema_contracts.SchemaCatalogTests.test_historical_contracts_route_only_approved_sources -v`

Expected: failure showing the old Quantt or provider-specific path.

- [ ] **Step 3: Update executable and prose contracts**

Set source consumers to `data_pipeline.rates_source` and `data_pipeline.futures_source`. Route all approved rate/futures migration rules to source-neutral destinations. Retain `r2_objects.csv` in place and exclude it from canonical manifests. Update only active plan/data-contract references; leave historical verification records unchanged.

```python
RATE_SOURCE_PATH = "data/source/rates/rates_YYYY.csv"
FUTURES_SOURCE_PATH = "data/source/futures/futures_settlements_YYYY.csv"
```

Introduce the two path constants immediately above `SCHEMAS`, replace only the
existing path argument and first consumer of each historical schema, and make
the affected migration rules reuse the same constants. Keep the existing
column, key, ordering, frequency, retention, and validation tuples unchanged.

- [ ] **Step 4: Run schema and migration coverage tests**

Run: `python -m unittest tests.test_schema_contracts -v`

Expected: all pass, including exact coverage of 1,487 audited artifacts.

- [ ] **Step 5: Commit Task 1**

```powershell
git add data_pipeline/contracts.py tests/test_schema_contracts.py docs/data/canonical-schemas.md docs/data/migration-preview.md docs/master-plan/MASTER_PLAN.md docs/master-plan/PROJECT_CONTRACTS.md
git commit -m "refactor: preserve source-neutral provenance"
```

### Task 2: Pure rate and futures canonicalizers

**Files:**
- Create: `data_pipeline/canonicalize.py`
- Create: `tests/test_canonical_migration.py`

**Interfaces:**
- Consumes: CSV `Mapping[str, str]` rows and literal source timing metadata.
- Produces: immutable effective-dated `SourceTiming`, `canonicalize_rates(path: Path) -> dict[int, list[dict[str, str]]]`, `canonicalize_futures(swap_path: Path, treasury_path: Path) -> FuturesCanonicalization`, and `canonicalize_daily_market(swap_prices_path: Path, treasury_prices_path: Path, timing_rules: Mapping[str, tuple[SourceTiming, ...]]) -> dict[int, list[dict[str, str]]]`. `FuturesCanonicalization` is immutable and exposes `settlements_by_year` and `risk_by_year`, each a `dict[int, list[dict[str, str]]]`; the named split is required because the two outputs have incompatible exact schemas.

- [ ] **Step 1: Write failing literal-fixture tests**

Create tiny rate, swap, Treasury-futures, swap-price, and Treasury-price CSVs. Assert exact output rows, basis-point conversion (`4.10` percent to `410` bp), provider identities (`UST`, `NYFED`, `ERIS`, `YAHOO`), stable instrument IDs, blank settlement DV01, contract-risk DV01, proxy labels, sorting, and rejection of duplicate, missing, nonfinite, nonpositive, or unknown columns.

```python
def test_rates_preserve_provider_and_convert_percent_to_basis_points(self) -> None:
    partitions = canonicalize_rates(self.fixture("treasury_rates.csv"))
    self.assertEqual(partitions[2026][0], {
        "observation_date": "2026-08-01",
        "source": "UST",
        "series_id": "DGS2",
        "maturity": "2Y",
        "rate_bps": "410",
    })
```

- [ ] **Step 2: Run canonicalizer tests and verify RED**

Run: `python -m unittest tests.test_canonical_migration.CanonicalizerTests -v`

Expected: import failure because `data_pipeline.canonicalize` does not exist.

- [ ] **Step 3: Implement minimal pure transforms**

Use `Decimal` for all numeric conversion. Declare exact source-column maps for only consumed 2Y/5Y rates and SOFR/EFFR. Require exact headers. Build immutable expiry-aware Eris IDs from ticker; retain root-only Treasury data as explicitly labelled continuous proxies. Set literal availability-time rules from the approved source matrix and reject dates for which no rule exists.

```python
class CanonicalizationError(ValueError):
    pass

@dataclass(frozen=True)
class SourceTiming:
    effective_from: date
    effective_to: date
    observation_time_utc: time
    availability_delay: timedelta
    source: str
    classification: str
    proxy_label: str = ""

RATE_COLUMNS = {
    "dgs2": ("UST", "DGS2", "2Y"),
    "dgs5": ("UST", "DGS5", "5Y"),
    "sofr": ("NYFED", "SOFR", "ON"),
    "effr": ("NYFED", "EFFR", "ON"),
}

def percent_to_bps(value: str) -> str:
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise CanonicalizationError("rate must be finite")
    return format(parsed * Decimal("100"), "f").rstrip("0").rstrip(".")
```

- [ ] **Step 4: Materialize and validate fixture partitions**

Write fixture outputs with a small test helper, then call `validate_csv` for `historical_rates`, `historical_futures_settlements`, `contract_risk`, and `daily_market`.

Run: `python -m unittest tests.test_canonical_migration.CanonicalizerTests -v`

Expected: all pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add data_pipeline/canonicalize.py tests/test_canonical_migration.py
git commit -m "feat: canonicalize approved market inputs"
```

### Task 3: Validated manifests

**Files:**
- Create: `data_pipeline/manifests.py`
- Modify: `tests/test_canonical_migration.py`

**Interfaces:**
- Consumes: validated canonical files and their `CsvContract`.
- Produces: `FileManifest`, `profile_file(repo_root: Path, path: Path, contract: CsvContract) -> FileManifest`, `write_input_manifest(path: Path, run_id: str, rows: Sequence[FileManifest]) -> str`, and `manifest_digest(rows: Sequence[FileManifest]) -> str`.

- [ ] **Step 1: Write failing manifest tests**

Assert literal SHA-256 for a known byte fixture, exact row count and coverage, schema version `1.0.0`, deterministic ordering by repository-relative path, rejection of unvalidated schema/header, and identical aggregate digest across repeated calls.

- [ ] **Step 2: Run manifest tests and verify RED**

Run: `python -m unittest tests.test_canonical_migration.ManifestTests -v`

Expected: import failure because `data_pipeline.manifests` does not exist.

- [ ] **Step 3: Implement manifest profiling**

Call `validate_csv` before hashing. Read bytes in fixed chunks. Derive start/end from the contract's date/time columns without accepting an empty file. Serialize manifests through their approved schemas and temporary siblings.

```python
@dataclass(frozen=True)
class FileManifest:
    path: str
    sha256: str
    row_count: int
    start_time: str
    end_time: str
    schema_version: str

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
```

- [ ] **Step 4: Run manifest and schema tests**

Run: `python -m unittest tests.test_canonical_migration.ManifestTests tests.test_schema_contracts -v`

Expected: all pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add data_pipeline/manifests.py tests/test_canonical_migration.py
git commit -m "feat: add canonical input manifests"
```

### Task 4: Deterministic staging and migration report

**Files:**
- Create: `data_pipeline/migration.py`
- Modify: `tests/test_canonical_migration.py`

**Interfaces:**
- Consumes: explicit repository/staging/report paths, canonicalizers, manifests, and `MIGRATION_RULES`.
- Produces: `stage_migration(repo_root, staging_root, report_path) -> MigrationResult` and CLI exit status.

- [ ] **Step 1: Write failing integration tests**

Build a temporary repository with all supported tiny inputs. Assert contained paths, rejection of symlink inputs and nonempty staging, exact report columns, rule-specific source/output key reconciliation, first/middle/last spot checks, unchanged input hashes, and byte-identical outputs from two independent staging roots. Patch socket operations before importing migration modules and fail the test on any network attempt.

```python
first = stage_migration(repo, repo / "stage-a", repo / "report-a.csv")
second = stage_migration(repo, repo / "stage-b", repo / "report-b.csv")
self.assertEqual(first.output_hashes, second.output_hashes)
self.assertEqual(before_input_hashes, hash_inputs(repo))
```

- [ ] **Step 2: Run staging tests and verify RED**

Run: `python -m unittest tests.test_canonical_migration.MigrationStagingTests -v`

Expected: import failure because `data_pipeline.migration` does not exist.

- [ ] **Step 3: Implement fail-closed staging**

Add strict path resolution, supported-input discovery, fresh staging creation, partition writing, validation, manifests, rule report rows, reconciliation, and a second temporary staging comparison. The CLI must omit publication entirely unless `--publish` is passed and every report row is `pass`.

```python
class MigrationError(RuntimeError):
    pass

@dataclass(frozen=True)
class MigrationResult:
    repo_root: Path
    staging_root: Path
    report_path: Path
    output_hashes: dict[str, str]
    all_passed: bool

def require_contained(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved_candidate = candidate.resolve(strict=False)
    if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
        raise MigrationError(f"path escapes repository: {candidate}")
    return resolved_candidate
```

- [ ] **Step 4: Run staging tests twice**

Run:

```powershell
python -m unittest tests.test_canonical_migration.MigrationStagingTests -v
python -m unittest tests.test_canonical_migration.MigrationStagingTests -v
```

Expected: both runs pass with identical fixture hashes.

- [ ] **Step 5: Commit Task 4**

```powershell
git add data_pipeline/migration.py tests/test_canonical_migration.py
git commit -m "feat: stage deterministic canonical migration"
```

### Task 5: Atomic publication and real-data dry run

**Files:**
- Modify: `data_pipeline/migration.py`
- Modify: `tests/test_canonical_migration.py`
- Create: `docs/verification/P24.md`
- Create: `docs/verification/P24-migration-report.csv`

**Interfaces:**
- Consumes: a passing `MigrationResult`.
- Produces: `publish_migration(result: MigrationResult, repo_root: Path) -> list[Path]`, canonical data files, manifests, and P24 evidence.

- [ ] **Step 1: Write failing publication tests**

Assert publication refuses any failed report row, preserves an existing destination when replacement fails, publishes only declared output paths, never modifies source files, and leaves no temporary sibling after success.

- [ ] **Step 2: Run publication tests and verify RED**

Run: `python -m unittest tests.test_canonical_migration.MigrationPublicationTests -v`

Expected: import or attribute failure for `publish_migration`.

- [ ] **Step 3: Implement atomic publication**

Copy each validated staged file to a destination sibling, validate/hash the sibling against the staged manifest, and replace the destination. Reject undeclared paths and resolved paths outside the repository. Do not move, delete, or archive any source.

```python
if not result.all_passed:
    raise MigrationError("publication requires a fully passing migration")
published: list[Path] = []
for relative_path, expected_hash in sorted(result.output_hashes.items()):
    source = require_contained(result.staging_root, result.staging_root / relative_path)
    destination = require_contained(repo_root, repo_root / relative_path)
    temporary = destination.with_name(f"{destination.name}.tmp")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, temporary)
    if sha256_file(temporary) != expected_hash:
        raise MigrationError(f"published hash mismatch: {relative_path}")
    temporary.replace(destination)
    published.append(destination)
return published
```

- [ ] **Step 4: Run a real-data staging pass without publication**

Run:

```powershell
python -m data_pipeline.migration --repo-root . --staging-root data/staging/p24 --report docs/verification/P24-migration-report.csv
```

Expected: exit zero, all report rows `pass`, original input hashes unchanged, and no canonical destination changed.

- [ ] **Step 5: Compare a second real-data staging pass**

Run the same command with `data/staging/p24-repeat` and
`docs/verification/P24-migration-report-repeat.csv`. Compare every staged
relative path and SHA-256. Expected: identical.

- [ ] **Step 6: Publish the passing staged result**

Run the first command again with `--publish`. Expected: only declared canonical
files and manifests are atomically written; originals and legacy results remain
byte-identical.

- [ ] **Step 7: Write P24 verification evidence**

Record exact commands, counts, coverage, hashes, representative spot checks,
determinism comparison, publication paths, recovery paths, network/broker/order/
cancel counts of zero, and all review findings. Request MG4 with P23 and P24
evidence; do not begin P30.

- [ ] **Step 8: Run the full suite and commit P24**

Run:

```powershell
python -m unittest discover -s tests -v
git diff --check
```

Expected: all tests pass and diff check exits zero.

```powershell
git add data_pipeline/canonicalize.py data_pipeline/manifests.py data_pipeline/migration.py tests/test_canonical_migration.py data/source data/canonical data/manifests
git add -f docs/verification/P24.md docs/verification/P24-migration-report.csv
git commit -m "feat: complete P24 canonical migration"
```
