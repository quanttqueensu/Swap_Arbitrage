# Strategy-Driven Paper Trader MVP Design

## Objective

Build a paper-only IBKR supervisor that repeatedly reconciles the existing 2Y
and 5Y daily strategy targets with a paper account. It must use the current
daily signal and risk output without changing the signal formula, historical
sizing logic, or backtest accounting.

## Scope

Included:

- 2Y (`YIT`/`ZT`) and 5Y (`YIW`/`ZF`) target execution only.
- A 30-second intraday polling loop during configured market hours.
- Last validated daily target remains fixed until a newer validated daily row
  is available.
- Paper IBKR connection, quotes, contract qualification, margin previews,
  positions, working orders, fills, reconciliation, audit records, and safe
  flattening.
- Existing signal/risk outputs as the target source, plus the existing pure
  runtime `strategy.risk_signals.evaluate_risk()` function for intraday gates.

Excluded:

- Live-account connections, live routing, production credentials, or a
  live-trading switch.
- Intraday signal recomputation, new data vendors, new signal models, and
  changes to 2Y/5Y signal or sizing equations.
- A native exchange-spread or combination-order implementation. This MVP
  manages two-leg paper order groups and explicitly handles partial fills.

## Architecture

Create `agents/agent_1/` as a standalone, signal-driven paper agent. Agent 0
remains an isolated random paper-order experiment and is not reused as policy.

```text
risk_data.csv -> daily target loader -> execution supervisor
                                            |
            IBKR paper quotes/positions/open orders/fills
                                            |
                          runtime risk adapter
                                            |
                             delta reconciler
                                            |
                       two-leg paper order groups
                                            |
                       paper audit data and state
```

The supervisor has four operator commands:

- `run`: poll during configured market hours.
- `once`: execute one complete supervisor cycle and exit.
- `status`: display target, actual positions, working orders, risk status, and
  target age.
- `stop-and-flatten`: cancel only Agent 1 working orders and reconcile its 2Y
  and 5Y positions to zero.

## Daily target source

The target loader reads the most recent row of `data/raw_data/risk_data.csv`.
It accepts a row only if all of the following are true:

- `risk_allowed` equals `1` and `risk_block_reason` is empty.
- The row has valid signed rounded quantities for both legs of each 2Y and 5Y
  maturity.
- The row date is within the configured maximum age in New York business days.
- The source file and accepted row form a stable target version, comprising the
  row date and SHA-256 hash of the row's execution-relevant fields.

The target is the exact desired position in qualified contracts, not a new
signal. A newer daily target supersedes the prior target only after full
validation. An invalid, stale, blocked, or unavailable target allows no new
risk. When Agent 1 is flat it stays flat; when it has exposure it begins its
normal flattening procedure.

## Paper-only configuration

`agents/agent_1/config.py` must enforce all of these at startup:

- localhost host, IBKR port `7497`, a `DU...` managed account, and a distinct
  Agent 1 client ID.
- `PAPER_ONLY = True` and `LIVE_TRADING_ENABLED = False` as immutable checks.
- America/New_York market-hours window and a 30-second polling interval.
- Positive limits for maximum target age, quote age, order-group timeout,
  maximum working order groups, maximum order groups per session, maximum
  gross and net DV01, maximum residual-DV01 fraction, margin reserve,
  maximum session loss, maximum drawdown, and each 2Y/5Y leg's absolute
  contract cap.

The required numeric limits live in an untracked local paper configuration
file named by an environment variable. Agent 1 must refuse to start when a
limit is missing, non-finite, zero where a positive value is required, or
outside its declared safe domain. The committed example contains no account
identifier or credentials.

## Supervisor cycle

Each cycle uses this sequence:

1. Read the operator stop state. If active, cancel Agent 1 orders and target
   zero positions.
2. Load and validate the fixed daily target.
3. Connect to and validate the paper session and managed account.
4. Qualify the exact contracts. Retain an eligible held contract for the
   target day; otherwise select the nearest eligible contract that satisfies
   the configured minimum days to expiry.
5. Request current bid/ask quotes, positions, Agent 1 working orders, and a
   margin preview for each proposed delta.
6. Reconcile the broker snapshot against persistent Agent 1 state. A mismatch
   blocks new exposure and starts flattening only after positions are known.
7. Map source risk targets and live state to the inputs of `evaluate_risk()`.
   A failed risk decision blocks new risk and requests flattening when exposure
   exists.
8. Calculate the signed delta from confirmed position plus Agent 1 working
   quantity to the desired target. A zero delta creates no order.
9. Submit at most one open two-leg order group per maturity. Persist the
   decision before submission and persist order identifiers immediately after
   the broker confirms them.
10. Record a fresh position, order, fill, and decision snapshot. The next poll
    starts from this recorded/broker-reconciled state rather than assuming a
    fill.

## Order-group policy

Each maturity has one order group containing its swap and Treasury futures
legs. All new orders use bid/ask-derived limit prices and a bounded timeout;
Agent 1 never submits paper market orders.

- Exposure reduction always precedes opening or reversing exposure.
- A maturity cannot have more than one active group.
- Before submitting, the group must pass quote validation, margin preview,
  per-leg cap, gross/net DV01, and residual-DV01 checks.
- If one leg fills or partially fills while its hedge does not, Agent 1 freezes
  expansion. It recalculates actual exposure and either submits the allowed
  hedge delta or flattens the filled leg when the residual cannot pass risk.
- A group timeout or rejection cancels the remaining Agent 1 group orders,
  records the cause, and lets the next cycle re-evaluate broker truth.
- Cancellation is scoped by Agent 1 order reference/client identity; it must
  never call global cancellation or alter manual/other-client orders.

## Failure policy

The agent fails closed for stale targets, malformed targets, stale/crossed
quotes, unavailable contract details, broker disconnection, account mismatch,
margin failure, reconciliation mismatch, invalid risk inputs, and state-write
failure.

When flat, a failure performs no trading. When an Agent 1 position exists, the
failure path cancels Agent 1 working orders, reads broker positions again, and
works the Agent 1 position to zero with bounded paper limit orders. It records
each failed and flattening decision. An operator stop file uses this same path.

## Auditability and state

Reuse the canonical paper CSV schemas and `PaperEventStore` for quotes, orders,
fills, and positions under `data/paper/agent_1/<run-id>/`. Add canonical
Agent-1 decision records that include target version, desired quantities,
observed quantities, risk result/reason codes, and action outcome.

Use one atomically replaced private state file for the current target version,
bound contracts, submitted order references, and last successful broker
snapshot. It is a recovery aid only: on restart, IBKR positions and open orders
remain authoritative.

## Verification and acceptance

The implementation must supply deterministic tests, with fake IBKR responses,
for:

- valid 2Y/5Y target loading and target-version stability;
- stale, blocked, malformed, and changed daily targets;
- duplicate polling without duplicate submission;
- contract binding and expiry rejection;
- limit-price construction, working-order accounting, and signed two-leg
  target deltas;
- risk blocks for quote, data-age, reconciliation, margin, DV01, loss,
  drawdown, capacity, and stop-file inputs;
- partial fills, timeout/rejection, restart recovery, and stop-and-flatten;
- strict Agent-1-only cancellation.

Acceptance requires the full repository suite to be green, `once` and
`status` to run against an IBKR paper account, and a market-hours paper soak
run that demonstrates signal-target loading, order submission, fill or safe
timeout handling, restart reconciliation, and stop-and-flatten without manual
state repair.

## Success criteria

The MVP is complete when it can run unattended in paper mode, repeatedly
reconcile the 2Y/5Y paper account to a fresh approved daily target without
duplicate orders, prevent new exposure on every listed risk failure, and leave
a complete audit trail that explains every action.
