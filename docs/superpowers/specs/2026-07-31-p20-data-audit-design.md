# P20 Current Data Audit Design

**Date:** 2026-07-31

**Phase:** Phase 2, prompt P20

**Base commit:** `1d1a7cdfc503daaa9bcfa0e43ae2dc30e141c869`

**Gate prerequisite:** MG2 approved 2026-07-31

## Purpose

P20 creates a reproducible, read-only audit of the repository's current CSV
pipeline, ignored working datasets, cached/source metadata, and column lineage.
It produces the evidence required to review the existing data estate before
P21 proposes canonical schemas or any migration action.

P20 does not move, copy, delete, rewrite, or canonicalize source data. It does
not implement P21 schemas. It stops for review after producing the inventory,
lineage, discrepancy list, and verification evidence.

## Workspace and data boundaries

Implementation is isolated on branch `codex/p20-data-audit`, created from the
approved MG2 commit. The audit receives two explicit roots:

- `--repo-root` points to the Phase 2 worktree and supplies tracked code and
  contracts for reader, writer, and consumer discovery.
- `--data-root` points to the main checkout and supplies the ignored `data/`
  tree and `r2_objects.csv` for read-only inspection.

The roots are never inferred from one another. Output paths must resolve under
the repository root. Input datasets are opened only for reading. The audit
records source hashes before and after a run, and the verification procedure
requires equality.

## Chosen approach

Use one focused Python command that combines deterministic profiling with a
small explicit lineage map for facts that source-code scanning cannot infer
reliably. This avoids a speculative scanner framework while keeping both
deliverables reproducible.

Automatic inspection supplies objective facts such as headers, row counts,
missingness, duplicate values, date coverage, and repository references.
Explicit annotations supply intent such as semantic classification, expected
units, known source columns, and whether an unresolved property is a
discrepancy. Inferred keys and units are labelled as candidates unless an
existing contract or code invariant makes them authoritative.

## Components

### Audit command

Add a script under `tools/` with a command-line interface that accepts:

```text
--repo-root PATH
--data-root PATH
--inventory-output PATH
--lineage-output PATH
```

The command validates both roots and refuses to place either output outside
`--repo-root`. It discovers current CSV artifacts under `data/`, includes the
top-level R2 object manifest when present, and scans tracked Python files for
CSV readers, writers, and column references. Discovery and output ordering are
stable by normalized relative path and column position.

### Inventory profiler

For every discovered CSV or relevant cached/source dataset, record:

- path, purpose, and source;
- byte size, row count, and ordered raw header;
- known units and unique key, or an explicitly labelled candidate/unknown;
- time column and inclusive time range where one can be established;
- duplicate headers and duplicate-key counts;
- ascending, descending, unsorted, or not-applicable sort status;
- per-column missingness and constant columns;
- exact duplicate columns based on equal values, not merely equal names; and
- code readers and writers with file-and-line evidence.

Raw headers are parsed before pandas can normalize duplicate names. The
current small working CSVs may be scanned fully. Large cache trees or future
large artifacts use file metadata and deterministic representative samples;
the report states the sampling rule and never presents sampled counts as full
counts.

### Column lineage

Generate one row for every column in:

- `raw_price_data.csv`;
- `signal_data.csv`;
- `risk_data.csv`; and
- every discovered `swap_arb_backtest_*.csv` output.

The CSV columns are:

```text
artifact,column,ordinal,classification,source_or_derivation,writer,consumers,unit,evidence,status
```

`classification` is exactly one of `source`, `canonical`, `feature`,
`decision`, `risk`, `accounting`, `diagnostic`, or `unused`. Multiple consumers
are stored as a stable semicolon-separated list. `status` distinguishes
`verified`, `candidate`, and `discrepancy` so an inference cannot be mistaken
for an approved contract.

The lineage explicitly highlights the current 24-to-40-to-72-to-99-column
widening and identifies columns copied forward without a downstream consumer.
Every claim contains code evidence or is marked as unresolved.

### Generated deliverables

The command writes:

- `docs/data/current-inventory.md`
- `docs/data/current-column-lineage.csv`

The inventory begins with the normalized command, roots, scope, sampling rules,
and aggregate counts. It then contains deterministic per-artifact profiles,
the pipeline-width comparison, cache/R2 summaries, and a final discrepancy
ledger. Execution time belongs in the separate verification record rather than
the generated reports. Machine-specific absolute input paths appear only in
the verification record; artifact identities use normalized paths relative to
the selected data root.

## Data flow

1. Validate roots and output containment.
2. Discover eligible data artifacts without following directory links.
3. Capture input metadata and hashes.
4. Parse raw headers and profile each artifact independently.
5. Scan tracked Python source for reader, writer, and column evidence.
6. Combine discovered evidence with the explicit lineage annotations.
7. Validate complete column coverage and classification vocabulary.
8. Render both outputs in stable order.
9. Re-hash every input and fail verification if any source changed.

## Error handling

Invalid roots, output escape attempts, or an inability to protect the
read-only boundary are fatal command errors. A malformed individual artifact
is recorded as a discrepancy with its exception type and concise message; the
audit continues so one bad file cannot erase findings for the remaining data.

Unknown units, ambiguous keys, missing timestamps, and unresolved lineage are
data findings rather than command crashes. They remain visible in the
deliverables and block promotion from `candidate` or `discrepancy` to
`verified`.

Outputs are written through temporary sibling files and replaced only after
the complete render validates. A failed run therefore does not leave a
partially written report.

## Testing strategy

Development follows red-green-refactor. Focused tests use temporary repository
and data roots and cover:

- root validation and output-containment rejection;
- raw duplicate-header detection;
- row counts, date ranges, missingness, constants, and sort status;
- duplicate candidate keys and exact duplicate columns;
- deterministic sampling and explicit sampled/full labels;
- reader, writer, and column-reference discovery;
- complete lineage rows and controlled classifications;
- malformed-file discrepancy recording without losing other profiles;
- byte-identical inputs before and after a run;
- stable output from two identical runs; and
- atomic output behavior on render failure.

Tests assert observable report content and filesystem effects rather than
private implementation structure. The existing approved test suite remains
green.

## Verification

P20 verification will:

1. run the focused audit tests;
2. run the approved full command,
   `python -m unittest discover -s tests -v`;
3. hash all audited inputs;
4. generate both reports twice from identical inputs and compare normalized
   output hashes;
5. confirm all input hashes are unchanged;
6. inspect the generated widening and unused-column findings against the
   actual headers and repository searches;
7. run `git diff --check`; and
8. record `git status --short` and the exact audit command.

After verification, the requested `ponytail-review` reviews the implementation
diff exclusively for unnecessary complexity. It reports simplification
opportunities but does not apply them automatically.

## Review and stopping point

P20 is ready for review when both deliverables regenerate deterministically,
the focused and full suites pass, the source hashes remain unchanged, and all
unresolved facts appear in the discrepancy ledger. The review checks data
quality counts and code lineage independently.

The work then stops. P21 schema design and migration preview do not begin until
the user reviews P20 as required by `PROMPT_PLAYBOOK.md`.
