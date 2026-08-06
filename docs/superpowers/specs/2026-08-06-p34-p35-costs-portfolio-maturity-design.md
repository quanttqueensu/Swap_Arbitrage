# P34/P35 Costs, Portfolio, and Maturity Scope Design

## Goal

Complete P34 as a pure standard-library strategy extension, then begin P35 by
auditing the maturity evidence without claiming support that the repository
cannot prove.

## Scope

P34 adds `strategy/costs.py`, `strategy/portfolio.py`, focused tests, public
exports, and `docs/verification/P34.md`. It reuses P31 cost normalization,
P32 ranking, P33 target positions, and P33 risk decisions. It does not change
the sizing equations, add a backtest, read files, use a clock, contact a
broker, or submit orders.

P35 follows P34 and records the evidence for 2Y, 5Y, 10Y, and 30Y in
`docs/verification/P35.md`. The active repository has no P11 source-coverage
matrix or canonical manifests, P24 explicitly removed durable manifests, MG4
and MG5 are not approved, and even 2Y/5Y lack all inputs required by P35.
Therefore P35 stops with exact blockers. It does not emit or freeze
`complete_2y_5y`, because that label would currently overstate support.

## Cost design

`CostEstimate` is a frozen, slotted record local to `strategy.costs`. It holds
six ordered `NamedValue` USD components, their USD total, and the total
normalized to basis points. `naive_cost` and `observed_cost` expose the same
keyword-only signature and share one internal calculation. Naive callers pass
approved fixed assumptions; observed callers pass directional causal USD
costs already derived from observed quotes. Both block on missing, non-finite,
negative, or wrong-type components and on nonpositive cost-base DV01.

The six names are `swap_bid_ask`, `treasury_bid_ask`,
`commission_exchange`, `slippage`, `roll`, and
`financing_not_in_funding`. No fallback is configured: the prompt permits
blocking, and blocking is the smallest fail-closed behavior. Roll cost is an
explicit close-plus-open USD input; existing `contract_turnover_contracts`
demonstrates close-and-open turnover without another helper.

## Portfolio design

`portfolio_dv01` returns the sum of existing target gross DV01 and residual
net DV01. `select_portfolio_targets` consumes P32 rank order plus unique P33
`TargetPosition` values. It greedily accepts each ranked target only when the
result remains at or below the gross limit and within the absolute net limit;
unsafe targets are skipped. The selected tuple preserves rank order.

This is deliberately not an optimizer. The master plan freezes ranking but
does not specify knapsack, netting, or rescaling behavior. A conservative
rank-first selector satisfies the risk rule with less code and no new policy.

## Validation and verification

Tests are written and observed failing before production code. They cover the
four approved synthetic directional examples, itemization, missing observed
costs, nonzero roll close/open costs, Decimal-context isolation, deterministic
selection, duplicate/malformed inputs, and monotonic risk limits. One pure
end-to-end example passes a cost result through existing opportunity, sizing,
ranking, portfolio, and risk functions. Fresh-process checks prove the new
modules load no pandas, IBKR, broker, order, network, file, or clock code.

P34 receives accounting, portfolio, requirements, quality, and final-branch
reviews. P35 receives data-coverage and contract/equations reviews. Final
verification runs the focused tests, all `docs/tests`, Agent 0 tests,
`compileall`, and `git diff --check`. No gate ledger row is changed without
explicit user approval.
