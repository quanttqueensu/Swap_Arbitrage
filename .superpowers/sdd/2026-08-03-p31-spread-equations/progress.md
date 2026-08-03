# SDD ledger — plan: docs/superpowers/plans/2026-08-03-p31-spread-equations.md

- Work directly on `main` by explicit user instruction; base commit `27cd4f0`.
- Ponytail full mode: prefer direct stdlib functions and reuse the frozen fixture.
- Review policy: request Luna high; runtime has no Luna, so use Terra high and never Sol.
- Task 1 review: Terra-high fallback found caller Decimal-context leakage; fixed test-first in `6b0a379`, scoped re-review APPROVE with no new breakage.
- Task 2 review: Terra-high fallback independently verified all hedge fixtures, rounding, signs, tie-break, validation, and residual math; APPROVE with no findings.

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

## Task 2 — Integer DV01 hedge and residuals

- RED: `python -m unittest docs.tests.test_spread -v` failed as expected with an import error for missing `dv01_hedge_quantities`.
- GREEN: focused suite passed 19 tests; docs suite passed 201 tests; Agent 0 suite passed 25 tests; `git diff --check` passed.
- Commit: `feat: add P31 DV01 hedge equations`.
- Caveat: this is exact explicit-input sizing only; it does not apply the 5% residual boundary or source production DV01 inputs.

## Task 3 — Basket P&L, turnover, and verification evidence

- RED: focused suite failed as expected with `ImportError: cannot import name 'basket_pnl_usd'` from `strategy.spread`.
- GREEN/final: focused suite passed 25 tests; docs suite passed 207 tests (with the existing `eventkit` deprecation warning); Agent 0 suite passed 25 tests; compileall and all four repository self-checks exited 0; `git diff --check` exited 0.
- Files: added direct `basket_pnl_usd` and `contract_turnover_contracts` functions and exports; added fixture-driven/boundary/context tests; created `docs/verification/P31.md`. Fixture SHA-256 remains `3fb9da5fc9ad255587ce93ea9770552f42566d56070f68ad1661709c030fbd76`.
- Caveats: explicit-input arithmetic only; P11 source matrix and production calendar remain unavailable. The existing ignored `agents/agent_0/orders` directory contains pre-existing July 2026 CSV fixtures; P31 neither used nor changed them. Final specialist and whole-branch review verdicts are intentionally left as evidence-only placeholders for the parent follow-up.
