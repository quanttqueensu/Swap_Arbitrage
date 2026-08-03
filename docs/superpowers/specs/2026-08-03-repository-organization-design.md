# Repository organization design

## Goal

Make the repository easier to navigate without changing runtime behavior or
the existing five-folder durable data layout.

## Approved structure

```text
agents/
  agent_0/
    runtime files
    tests/

data_pipeline/
  historical_data/
    historical_data_builder.py
    canonicalize.py
  live_data_pipeline/
    IBKR paper-data scripts
  shared schema contracts at data_pipeline/contracts.py

docs/
  tests/
  tools/
  superpowers/
  active project/research documentation
  archive/ for non-runtime historical artifacts

data/
  raw_data/
  futures/
  rates/
  market/
  contract_risk/
```

Historical source ingestion lives under `data_pipeline/historical_data/`.
Downstream strategy stages remain at the repository root with purpose-based
names: `signal_pipeline.py`, `risk_pipeline.py`, `backtest_engine.py`,
`clean_data.py`, and `config.py`.

## Moves

- Move `data_pipeline/canonicalize.py` into
  `data_pipeline/historical_data/`.
- Move `historical_data_builder.py` into
  `data_pipeline/historical_data/`.
- Move `data_pipeline/ibkr_paper_source.py` and `data_pipeline/paper_store.py`
  into `data_pipeline/live_data_pipeline/`.
- Move `tests/` into `docs/tests/`; move the Agent 0 characterization test into
  `agents/agent_0/tests/` because it is agent-specific.
- Move `tools/data_audit.py` into `docs/tools/`.
- Move `.superpowers/sdd/` into `docs/superpowers/sdd/`.
- Move the non-runtime R2 helper and inventory into `docs/archive/`.

Every moved Python package receives an `__init__.py` where needed. Imports,
README commands, and documentation references are updated to the new paths.
The old paths are not retained as compatibility shims.

## Invariants

- `data/` retains exactly `raw_data`, `futures`, `rates`, `market`, and
  `contract_risk`.
- No runtime module imports from `docs`.
- The full test suite remains discoverable from `docs/tests`.
- Agent 0 tests remain discoverable from `agents/agent_0/tests`.
- Canonical CSV bytes and raw input/cache contents are not rewritten.
- No Cloudflare/R2 integration is added.
