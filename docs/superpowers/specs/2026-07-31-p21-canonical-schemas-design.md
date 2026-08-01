# P21 Canonical Schemas and Migration Preview Design

## Scope and constraints

P21 freezes narrow CSV contracts and previews migration actions. It does not
move, rewrite, archive, supersede, or delete any audited artifact. The design
uses the MG2-approved contracts and equation clock, the P20 inventory of 1,487
CSV artifacts, and the P20 lineage of 518 wide-pipeline columns. All work is
offline and preserves the permanent paper-only boundary.

## Considered approaches

1. **Executable schema catalog (recommended).** Store immutable schema metadata
   in `data_pipeline/contracts.py`, validate small CSV fixtures with a generic
   standard-library validator, and render the human contract explicitly in
   `docs/data/canonical-schemas.md`. This gives P22/P23 reusable contracts and
   lets tests detect drift without touching real data.
2. **Documentation only.** Put every contract in Markdown and add tests that
   merely inspect samples. This is smaller initially, but P22 would have to
   reinterpret the tables and could diverge.
3. **Markdown as runtime input.** Parse contract tables directly. This creates
   one textual source but couples validation to document formatting and adds a
   fragile parser with no strategy consumer.

The selected approach is option 1. It is the smallest approach that makes the
P21 freeze executable and reusable without beginning ingestion or migration.

## Schema catalog

`data_pipeline/contracts.py` will define frozen `ColumnContract` and
`CsvContract` records, a mapping keyed by schema ID, and a pure
`validate_csv(contract, path)` function. A contract records:

- schema ID and semantic version;
- path pattern and partitioning;
- ordered column names, scalar types, units, and nullability;
- unique key and deterministic ordering;
- update frequency, retention, named consumers, and validation rules.

Validation is intentionally bounded to P21: exact header, scalar parsing,
required values, unique keys, ordering, ISO dates/UTC timestamps, identity
rules such as exactly one of `series_id` and `instrument_id`, positive numeric
fields where required, and bid/ask consistency. Atomic writing, source access,
canonicalization, and manifest creation remain P22-P24 work.

The catalog retains the schemas already named in `PROJECT_CONTRACTS.md` and
adds only fields required by an approved consumer. In particular, market rows
carry `available_at_utc` because P10 decisions use publication availability,
and classification/proxy fields preserve exact/proxy/assumed/unavailable
lineage. Paper and backtest decisions, orders, fills, positions, daily results,
trades, summaries, run manifests, and input manifests remain separate narrow
tables rather than one wide result.

All schemas use version `1.0.0`. A later incompatible field, key, unit, or
meaning change requires a new schema version under the project change-control
contract.

## Migration preview

`docs/data/migration-preview.md` will define non-overlapping rules whose union
matches every P20 artifact:

- 1,474 Eris vendor settlement cache CSVs: keep immutable source; later ingest
  only consumed fields into year-partitioned source/canonical files.
- `cme_swap_data.csv`, `treasury_futures_data.csv`, `swap_rates.csv`,
  `treasury_futures.csv`, and `treasury_rates.csv`: keep unchanged now and
  regenerate into named narrow source/reference/market contracts after source
  validation.
- `raw_price_data.csv`, `signal_data.csv`, and `risk_data.csv`: keep unchanged
  now and supersede only after canonical and strategy replacements validate.
- four wide backtest CSVs: keep unchanged now and later archive as labelled
  legacy proxy results after replacement reports reconcile.
- `r2_objects.csv`: keep as inventory metadata; it is not a canonical market
  input.

Each rule records current and expected rows/columns, staging destination,
validation prerequisite, and recovery from the untouched original. A coverage
test parses the P20 inventory and proves every audited path matches exactly one
preview rule. Raw cache retention is conservative: no automatic deletion;
cleanup requires the later approved consolidation prompt.

## Tests and verification

Tests use temporary CSV fixtures only. The RED step imports the absent schema
catalog. GREEN covers valid samples and representative failures for headers,
types, missing values, duplicate keys, ordering, causal timestamps, identity
exclusivity, and crossed quotes. Contract completeness tests require every
path, version, column, unit, type, key, ordering rule, update frequency,
retention rule, and consumer list. Migration tests prove exact one-rule
coverage for all 1,487 P20 artifacts and reject destructive or action-performing
language.

P21 verification runs focused schema tests, full unittest discovery, existing
self-checks, compilation, `git diff --check`, and `git status --short`. Fresh
read-only requirements, schema, quality, and migration-safety reviews precede
the MG3 stop.

## Non-goals

P21 does not contact Cloudflare/R2 or IBKR, read broker state, submit or cancel
orders, canonicalize real rows, create staging data, alter the main checkout,
or approve MG3. P22 begins source ingestion only after the user approves the
P21 contracts and migration preview.
