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

## Task 3 review correction â€” P2 basket evidence

- Findings: the altered future leg was unused; the low-precision basket test restored rather than asserted the caller context; and the P31 hedge tie table abbreviated production output as `/ same`.
- RED: two temporary mutants were restored. Dropping the future leg failed normal full-basket USD `13.0000` rather than USD `23`; the targeted causal-control mutant zeroed the entire altered new-leg price difference and failed USD `13.0000` rather than literal USD `2023` (exit 1, one failure). This was not a bump-only mutation: suppressing only the added `Decimal("1")` endpoint bump would make altered full basket equal normal full basket USD `23`.
- GREEN: restored direct P&L arithmetic; strengthened control proves old-only boundary USD `13` is unchanged while the altered new leg changes full basket USD `23` to USD `2023`; context state is asserted before restoration; tie tuple is complete. Focused suite passed 25 tests; docs suite passed 207 tests; `git diff --check` passed.
- Commit: `fix: strengthen P31 basket evidence` (separate review-correction commit; no amend).

## Task 3 evidence correction â€” mutation description

- Corrected P31 and this ledger to distinguish the actual temporary mutations from a hypothetical bump-only mutation. No runtime or test code was changed; the direct P&L expression remains restored.
- Verification: focused suite and `git diff --check` were rerun for this evidence-only correction.
- Commit: `docs: correct P31 mutation evidence` (separate evidence-only commit).

## Task 2 numerical review correction — Decimal context isolation

- Finding: numerical specialist returned CHANGES_REQUIRED on Task 2 context-test coverage while initially judging production arithmetic sound.
- RED: a temporary isolation-bypass mutant failed all three new precision-2 subtests: hedge `(2, -2)` versus `(1, -1)`, residual `-6.2E+2` versus `-627.489`, and fraction `4.2` versus the literal 50-digit result; the mutant was restored.
- Restored-code RED: the regression then exposed `abs(net)` rounding before the precision-50 helper, yielding `4.2143287176399759181216134858518964479229379891632`; `net.copy_abs()` fixed the leak without changing the public API.
- GREEN: full context settings/flags/traps remain unchanged around all three Task 2 functions; focused suite passed 26 tests; docs suite passed 208 tests; `git diff --check` passed.
- Commit: `test: harden P31 hedge context isolation`.
- Scoped re-review: verified literal `(1, -1)`, residual `-627.489`, and fraction `4.1975316074653822998193859121011438892233594220349`; APPROVE with no findings.

## P31 specialist verdicts

- Equations reviewer: Terra-high fallback (Luna unavailable; no Sol) independently matched economic 4/4, hedge/residual/allowance 7/7, P&L/turnover 4/4, quote ticks 4/4, Treasury endpoints 4/4, and schema/hash 1/1; APPROVE. P11 source matrix and production calendar remain unavailable.
- Numerical reviewer: initial CHANGES_REQUIRED for missing full caller-context tests on all three Task 2 functions; `a55ec6b` added precision-2 full-context subtests and minimally replaced the exposed `abs(net)` precision leak with `copy_abs()`. Scoped re-review verified the exact literals above; APPROVE with no findings.
- Ponytail simplicity reviewer: Terra high suggested deleting 28 lines of duplicated expected/production evidence tables. Not applied because the approved Task 3/MG5 plan explicitly requires row-by-row evidence; no runtime abstraction, dependency, or code finding. This is not an unconditional `Lean already` verdict.
- Task 3 scoped review: APPROVE after causal-control, context, and evidence corrections.
- Whole-branch review: explicitly pending.
- Commit: `docs: record P31 specialist reviews`.
