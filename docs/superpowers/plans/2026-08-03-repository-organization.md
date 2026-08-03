# Repository organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize runtime-adjacent code, agent files, tests, tools, and historical project artifacts into explicit folders without changing behavior.

**Architecture:** Keep the repository root as the small runtime entry-point surface. Split `data_pipeline` into historical and live baskets, keep shared schema contracts at its root, colocate Agent 0 tests with Agent 0, and place tests/tools/chat artifacts under `docs`.

**Tech Stack:** Python standard library, pandas/numpy runtime, unittest discovery, PowerShell file moves, Git.

## Global Constraints

- Preserve the existing `data/` five-folder layout.
- Do not rewrite CSV bytes or raw/cache contents.
- Do not add Cloudflare/R2 runtime integration.
- Do not leave compatibility shims at old paths.
- Keep runtime modules from importing `docs`.

---

### Task 1: Freeze the target tree and move package files

**Files:**
- Create: `data_pipeline/historical_data_pipeline/__init__.py`
- Create: `data_pipeline/live_data_pipeline/__init__.py`
- Move: `data_pipeline/canonicalize.py` to `data_pipeline/historical_data_pipeline/canonicalize.py`
- Move: `data_pipeline/ibkr_paper_source.py` to `data_pipeline/live_data_pipeline/ibkr_paper_source.py`
- Move: `data_pipeline/paper_store.py` to `data_pipeline/live_data_pipeline/paper_store.py`

- [x] Move the three pipeline modules and add package markers.
- [x] Search all imports and record every old module path before changing callers.
- [x] Confirm no CSV file under `data/` changes during the move.

### Task 2: Rehome agent and development-only files

**Files:**
- Move: `tests/test_agent_0_characterization.py` to `agents/agent_0/tests/test_characterization.py`
- Create: `agents/agent_0/tests/__init__.py`
- Move: remaining `tests/` files to `docs/tests/`
- Move: `tools/data_audit.py` to `docs/tools/data_audit.py`
- Create: `docs/tools/__init__.py`
- Move: `.superpowers/sdd/` to `docs/superpowers/sdd/`
- Move: `r2_database_names.py` and `r2_objects.csv` to `docs/archive/`

- [x] Move general tests and fixtures to `docs/tests`.
- [x] Move Agent 0 characterization into the agent package.
- [x] Move audit tooling and historical R2 artifacts out of the runtime surface.
- [x] Leave only the approved runtime/configuration files at repository root.

### Task 3: Repair imports and test discovery

**Files:**
- Modify: `data_pipeline/historical_data_pipeline/canonicalize.py`
- Modify: `data_pipeline/live_data_pipeline/ibkr_paper_source.py`
- Modify: `data_pipeline/live_data_pipeline/paper_store.py`
- Modify: all moved tests and runtime callers that import moved modules
- Modify: `docs/tests/test_data_audit.py`
- Modify: `README.md`, `requirements.txt`, and active docs with test commands

- [x] Replace `data_pipeline.canonicalize` imports with `data_pipeline.historical_data_pipeline.canonicalize`.
- [x] Replace live recorder/store imports with `data_pipeline.live_data_pipeline.*`.
- [x] Replace `tools.data_audit` imports with `docs.tools.data_audit`.
- [x] Update audit inventory paths for `docs/archive/r2_objects.csv`.
- [x] Update test commands to use `-s docs/tests` plus the Agent 0 test package.
- [x] Add focused import/layout tests for the new paths.

### Task 4: Verify the organized repository

**Files:**
- Verify: all moved files, imports, and docs
- Test: `docs/tests` and `agents/agent_0/tests`

- [x] Run `git diff --check`.
- [x] Run Python compilation for moved modules and tests.
- [x] Run the complete discoverable suite and report any pre-existing optional dependency failures separately.
- [x] Confirm `data/` has exactly five folders and canonical CSV hashes are unchanged.
- [x] Confirm no runtime Python file imports from `docs`.

### Task 5: Commit on main

**Files:**
- Commit all intended moves and import/documentation updates.

- [x] Review `git status` and rename detection.
- [x] Stage the organization changes.
- [x] Commit on `main` with a focused message.
