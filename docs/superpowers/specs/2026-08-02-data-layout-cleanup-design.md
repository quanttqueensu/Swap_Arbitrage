# Data Layout Cleanup Design

## Goal

Organize the durable data set into five explicit data classes and remove the
manifest/staging subsystem and its persisted artifacts.

## Durable layout

```text
data/raw_data/       # original wide inputs and legacy backtest CSVs
data/futures/        # canonical futures settlement partitions
data/rates/          # canonical rate partitions
data/market/         # canonical daily market partitions
data/contract_risk/  # canonical contract-risk partitions
```

`data/manifests/` and `data/staging/` are removed. No compatibility copies or
aliases are retained. The existing raw and canonical CSV bytes are moved
without transformation.

## Code boundary

Pure canonicalization remains available through `data_pipeline.canonicalize`.
The manifest module, migration/publication orchestrator, P24 run/input
artifacts, migration reports, and their manifest/staging tests are removed.
Configuration, schema contracts, audit tooling, and documentation use the new
durable paths directly.

## Safety and verification

- Before each move, record source SHA-256 values and verify destination bytes.
- Raw inputs remain immutable and are read from `data/raw_data/`.
- Canonical outputs are validated against their existing schemas in their new
  folders.
- Test fixtures use temporary repositories with the new layout.
- The full test suite and `git diff --check` must pass before commit.

## Out of scope

This cleanup does not change row contents, schema columns, provider labels,
canonicalization rules, strategy behavior, broker behavior, or the historical
R2 inventory outside `data/`.
