# P40B Technical Documentation Completion Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the newly authorized P40B onboarding document, reconcile the audit/gate evidence, and verify every command presented as supported.

**Architecture:** Add one repository-grounded Markdown entry point that links to existing authoritative contracts rather than copying them. Keep all previous P40B code and ignored findings unchanged; update only the documentation and evidence needed for the new authorization.

**Tech Stack:** Markdown, Python 3.12, existing `unittest` suites and repository self-checks.

## Global Constraints

- Preserve unrelated working-tree changes and paper-only protections.
- Do not connect to external systems or transmit broker orders.
- Do not change TF-001 or TF-005 through TF-009/TF-011.
- TF-010 is now explicitly authorized only to create the required onboarding entry point.
- Use repository behavior and authoritative project contracts as the source of truth.
- Document volatile external facts only when runtime behavior actually depends on them.
- Add no dependency, abstraction, speculative optimization, or unapproved cleanup.

---

### Task 1: Map runnable interfaces and authoritative references

**Files:**
- Read: `README.md`, `requirements.txt`, `.gitignore`
- Read: `docs/master-plan/PROJECT_CONTRACTS.md`, `docs/master-plan/VERIFICATION_GATES.md`
- Read: `data_pipeline/contracts.py`, `backtesting/*.py`, `agents/agent_0/*.py`
- Read: supported test/self-check entry points

**Interfaces:**
- Consumes: current repository behavior and existing verification records.
- Produces: a checked inventory of commands, ownership boundaries, data flow, safety invariants, and authoritative links.

- [ ] **Step 1: Inventory directories, entry points, and commands**

Use `rg --files -uu`, targeted `rg`, and read-only file inspection. Do not infer a command from an aspirational plan.

- [ ] **Step 2: Trace canonical-data, strategy, replay, report, and Agent 0 paths**

Record only paths that exist. Label missing canonical-to-backtest adapters and realistic backtesting as deferred.

- [ ] **Step 3: Verify runtime-dependent package/API facts**

Use pinned requirements and local package/source behavior. Add primary-source links and a 2026-08-09 verification date only for external facts used by the current paper boundary.

### Task 2: Create the onboarding entry point

**Files:**
- Create: `docs/TECHNICAL_DOCUMENTATION.md`

**Interfaces:**
- Consumes: Task 1 inventory and authoritative project documents.
- Produces: the primary contributor entry point required by P40B.

- [ ] **Step 1: Write Quick start**

Cover purpose, permanent paper-only boundary, supported Python setup, verified commands, and a compact architecture map.

- [ ] **Step 2: Write System reference**

Cover ownership, data flow/provenance, strategy-to-risk flow, backtest artifacts, Agent 0 lifecycle, testing, and fail-closed behavior.

- [ ] **Step 3: Write Technical reference**

Summarize and link equations/units/signs/timing, configuration/dependencies, required external facts, troubleshooting, deferred work, and glossary.

- [ ] **Step 4: Check links and unsupported claims**

Search every local Markdown target and every command. Remove aspirational commands from the supported quick start.

### Task 3: Reconcile P40A/P40B evidence

**Files:**
- Modify: `docs/audits/technical-foundation-audit.md`
- Modify: `docs/verification/P40.md`
- Modify: `docs/master-plan/VERIFICATION_GATES.md`

**Interfaces:**
- Consumes: Task 2 artifact and final verification.
- Produces: final TF-010 disposition, approved-versus-actual summary, and MG6A completion-sign-off evidence.

- [ ] **Step 1: Mark TF-010 superseded and implemented**

Preserve the earlier ignore decision historically, then record the explicit P40B authorization and exact document path.

- [ ] **Step 2: Record actual scope**

List TF-002 option C, TF-003 option A, TF-004 relabeling, and TF-010 documentation. Keep every other disposition unchanged.

- [ ] **Step 3: Record verification honestly**

Do not call ignored diff/environment findings passed. State the runtime/site-package arrangement used.

### Task 4: Final verification and review

**Files:**
- Verify: all files in the final diff

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: the MG6A completion-sign-off packet.

- [ ] **Step 1: Run every supported documentation command**

Run environment inspection, focused/full tests, schema tests, compileall, and all three self-checks exactly as documented.

- [ ] **Step 2: Run repository checks**

Run local-link/reference checks, secret-safe scans, `git diff --check`, and `git status --short`. Record existing failures without changing ignored findings.

- [ ] **Step 3: Request fresh reviews**

Reuse one lower-tier general reviewer for consolidated repository/documentation quality and one lower-tier specialist for accounting/schema/broker-safety claims. Resolve actionable in-scope findings and rerun affected checks.

- [ ] **Step 4: Produce completion sign-off**

Report audit dispositions, approved-versus-actual scope, verification evidence, the onboarding path, and remaining deferred/accepted issues.
