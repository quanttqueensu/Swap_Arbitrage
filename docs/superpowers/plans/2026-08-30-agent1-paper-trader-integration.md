# Agent 1 Paper Trader Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the supplied strategy-driven Agent 1 paper trader into the repository, repair the stale repository-layout check, document operation, and verify every offline safety boundary.

**Architecture:** Keep Agent 1 isolated under `agents/agent_1/` and reuse the existing `strategy.risk_signals.evaluate_risk()` and `PaperEventStore` boundaries. Mechanically import the already-tested attachment, make only repository-integration changes around it, and retain fail-closed behavior for stale targets, missing contract risk, or unavailable IBKR paper state.

**Tech Stack:** Python 3.12, standard-library `unittest`, pandas 3.0.1, NumPy 2.3.5, ib_insync 0.9.86.

**Spec:** `docs/superpowers/specs/2026-08-29-strategy-driven-paper-trader-design.md`

## Global Constraints

- Preserve the user's staged `.env`, `.gitignore`, IDE cleanup, cache cleanup, and Agent 0 order-file cleanup.
- Work only with IBKR paper host `127.0.0.1`, port `7497`, a `DU...` account, and an Agent 1 client ID distinct from Agent 0 client `30`.
- Do not add a live-trading switch, market orders, global cancellation, credentials, account identifiers, or invented market/risk data.
- Do not change the strategy equations, historical sizing logic, or backtest accounting.
- Use `.venv/Scripts/python.exe`; bare `python` is not available on this host.
- Do not delete `data/results/backtests/historical-refresh/`; it is documented generated output and contains user backtest results.
- Do not commit automatically from this dirty shared checkout; leave a reviewable working-tree diff.

---

### Task 1: Import the tested Agent 1 package

**Files:**
- Create: `agents/agent_1/*.py`
- Create: `agents/agent_1/tests/*.py`
- Create: `agents/agent_1/agent1.paper.example.json`

**Interfaces:**
- Consumes: `strategy.risk_signals.evaluate_risk` and `data_pipeline.live_data_pipeline.paper_store.PaperEventStore`
- Produces: `python -m agents.agent_1.run {status,once,run,stop-and-flatten}`

- [ ] **Step 1: Verify the package is absent in the repository**

Run:

```powershell
& .\.venv\Scripts\python.exe -m unittest agents.agent_1.tests.test_config -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agents.agent_1'`.

- [ ] **Step 2: Mechanically import the attachment**

Extract only `agent_1_impl/agents/agent_1/` from `C:\Users\jaydo_0v7vk2o\Downloads\agent_1_impl (1).zip` and place its contents at `agents/agent_1/`. Do not import the attachment's top-level README, verification claims, or checklist as executable project instructions.

- [ ] **Step 3: Run the focused configuration test**

Run:

```powershell
& .\.venv\Scripts\python.exe -m unittest agents.agent_1.tests.test_config -v
```

Expected: 7 tests pass.

- [ ] **Step 4: Run the entire imported suite and compile it**

Run:

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s agents/agent_1/tests -v
& .\.venv\Scripts\python.exe -m compileall -q agents/agent_1
```

Expected: 105 tests pass and compileall exits 0.

- [ ] **Step 5: Enforce the paper-only source boundary**

Run:

```powershell
rg -n "reqGlobalCancel|MarketOrder" agents/agent_1 -g "*.py" -g "!tests/**"
rg -n "placeOrder" agents/agent_1 -g "*.py" -g "!tests/**"
```

Expected: no global-cancel or market-order match; the only production `placeOrder` call is the reviewed call in `execution.py`.

### Task 2: Repair the generated-data layout regression

**Files:**
- Modify: `docs/tests/test_data_layout.py`
- Test: `docs/tests/test_data_layout.py`

**Interfaces:**
- Consumes: documented canonical input directories and optional generated `results`/`paper` directories
- Produces: a layout guard that still rejects unknown durable folders without rejecting supported runtime output

- [ ] **Step 1: Reproduce the existing failure**

Run:

```powershell
& .\.venv\Scripts\python.exe -m unittest docs.tests.test_data_layout.DataLayoutTests.test_durable_data_has_only_the_requested_folders -v
```

Expected: FAIL because `data/results` exists.

- [ ] **Step 2: Make the smallest correction to the layout assertion**

Replace the exact-folder assertion with an allowlist that separates canonical durable inputs from optional generated output:

```python
def test_data_has_only_canonical_and_generated_folders(self) -> None:
    folders = {path.name for path in DATA_DIR.iterdir() if path.is_dir()}
    generated = {"paper", "results"}
    self.assertEqual(
        folders - generated,
        {"raw_data", "futures", "rates", "market", "contract_risk"},
    )
```

- [ ] **Step 3: Verify the focused layout suite**

Run:

```powershell
& .\.venv\Scripts\python.exe -m unittest docs.tests.test_data_layout -v
```

Expected: 5 tests pass.

### Task 3: Document Agent 1 as a supported paper-only runtime

**Files:**
- Modify: `README.md`
- Modify: `docs/TECHNICAL_DOCUMENTATION.md`

**Interfaces:**
- Consumes: `AGENT1_PAPER_CONFIG`, the committed account-free example, and the four Agent 1 CLI commands
- Produces: reproducible offline verification and safe operator setup instructions

- [ ] **Step 1: Add Agent 1 to the canonical test command**

Extend the README test block with:

```powershell
python -m unittest discover -s agents/agent_1/tests -v
```

- [ ] **Step 2: Add concise Agent 1 setup and command documentation**

Document that the operator must copy `agents/agent_1/agent1.paper.example.json` to an untracked private path, set `AGENT1_PAPER_CONFIG`, start with `status`, and use only:

```powershell
python -m agents.agent_1.run status
python -m agents.agent_1.run once
python -m agents.agent_1.run run
python -m agents.agent_1.run stop-and-flatten
```

State explicitly that current target/risk data may fail closed and that real TWS/Gateway acceptance is not an offline verification claim.

- [ ] **Step 3: Add Agent 1 to the technical component and testing references**

Describe `agents/agent_1/` as the strategy-driven paper supervisor, separate from Agent 0's random experiment, and list its deterministic suite beside the existing repository suites.

- [ ] **Step 4: Verify documentation references and CLI exposure**

Run:

```powershell
rg -n "agent_1|AGENT1_PAPER_CONFIG|stop-and-flatten" README.md docs/TECHNICAL_DOCUMENTATION.md
& .\.venv\Scripts\python.exe -m agents.agent_1.run --help
```

Expected: both documents contain the operator boundary and help lists exactly `run`, `once`, `status`, and `stop-and-flatten`.

### Task 4: Run full offline acceptance

**Files:**
- Verify: all modified and imported files

**Interfaces:**
- Consumes: the complete repository, imported Agent 1 package, and pinned local environment
- Produces: fresh offline verification evidence and a precise list of external acceptance blockers

- [ ] **Step 1: Verify dependencies and offline self-checks**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe signal_pipeline.py --self-check
& .\.venv\Scripts\python.exe risk_pipeline.py --self-check
& .\.venv\Scripts\python.exe -m backtesting --self-check
```

Expected: every command exits 0.

- [ ] **Step 2: Run all repository test suites**

Run:

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s docs/tests -v
& .\.venv\Scripts\python.exe -m unittest discover -s agents/agent_0/tests -v
& .\.venv\Scripts\python.exe -m unittest discover -s agents/agent_1/tests -v
```

Expected: 322 documentation/strategy tests, 29 Agent 0 tests, and 105 Agent 1 tests pass (456 total).

- [ ] **Step 3: Review the final diff and workspace preservation**

Run:

```powershell
git status --short
git diff --check
git diff -- README.md docs/TECHNICAL_DOCUMENTATION.md docs/tests/test_data_layout.py
git diff --stat
```

Expected: no whitespace errors; existing staged cleanup remains intact; new Agent 1 files and this plan are visible for review.

- [ ] **Step 4: Record external acceptance blockers without bypassing them**

Report, without mutating market data, that live acceptance still needs a private `DU...` configuration, TWS/Gateway on paper port 7497, a fresh validated `risk_data.csv` row, matching contract-risk IDs for bound contracts, and the controlled paper soak/restart/partial-fill/flatten exercises from the approved spec.
