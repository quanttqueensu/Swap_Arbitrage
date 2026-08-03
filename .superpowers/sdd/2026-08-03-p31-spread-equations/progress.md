# SDD ledger — plan: docs/superpowers/plans/2026-08-03-p31-spread-equations.md

- Work directly on `main` by explicit user instruction; base commit `27cd4f0`.
- Ponytail full mode: prefer direct stdlib functions and reuse the frozen fixture.
- Review policy: request Luna high; runtime has no Luna, so use Terra high and never Sol.

## Task 1 — Units, spreads, funding, costs, and net

- RED: `python -m unittest docs.tests.test_spread -v` failed as expected with `ModuleNotFoundError: No module named 'strategy.spread'` (1 import error).
- GREEN: focused suite passed 11 tests; `unittest discover -s docs/tests -v` passed 193 tests; `git diff --check` passed.
- Files: added `strategy/spread.py` and `docs/tests/test_spread.py`; updated `strategy/__init__.py`; fixture SHA-256 remains `3fb9da5fc9ad255587ce93ea9770552f42566d56070f68ad1661709c030fbd76`.
- Commit: `feat: add P31 spread and funding equations`.
- Caveat: this is explicit-input arithmetic only; the P11 source matrix and production calendar remain unavailable.

## Task 1 follow-up — Decimal caller-context isolation

- Finding: direct Decimal arithmetic inherited caller precision and set caller flags outside `localcontext()`.
- RED: precision-2 regression failed for all 9 Task 1 public functions (`rate`, `quote`, `tick`, `fixed`, `funding`, `expected`, `gross`, `cost`, `net`).
- GREEN: the regression passed (1 test); focused suite passed 12 tests; docs suite passed 194 tests; `git diff --check` passed.
- Commit: `fix: isolate P31 Decimal arithmetic`.
