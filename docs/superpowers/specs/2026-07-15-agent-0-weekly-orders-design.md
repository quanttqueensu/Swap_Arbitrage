> **Superseded (2026-07-29):** This is a historical design record. Its
> 20-orders/day (100/week) proposal is superseded by MG1's authoritative
> 5-orders/day (25/week) selection. The historical body below is unchanged.

# Agent 0 Weekly Orders Design

## Goal

Running `agents/agent_0/run.py` once attempts to queue one week of random
paper-market orders with IBKR while preserving a 10% margin reserve. Accepted
orders remain at IBKR and activate without Python or Trader Workstation
remaining open; margin-blocked orders remain locally planned.

## Schedule

- Target the Monday through Friday of the next calendar week.
- Create exactly 20 orders per target day, for 100 orders total.
- Randomize each activation time between 09:00 and 15:00
  `America/New_York`, a common tradable window for every configured future.
- Submit each order immediately with IBKR `GoodAfterTime`; use a transmitted
  `MKT` order with `DAY` time in force.
- If IBKR rejects an order, stop submission and report the partial result.

## IBKR Working-Order Capacity

IBKR permits at most 15 working orders on one side of one futures contract.
Agent 0 therefore uses the nearest three valid expiries for each positive-cap
symbol instead of routing every order to the front contract.

Before any new order is submitted, Agent 0 will:

1. Read every currently working IBKR order for the configured account.
2. Count occupied slots by contract ID and side, including non-Agent-0 orders.
3. Resolve and qualify the nearest three valid contracts for every symbol used
   by the plan.
4. Assign all unsubmitted records to contract-side buckets with fewer than 15
   working orders, using the least occupied eligible bucket first.
5. Abort before submitting anything if the complete remaining plan cannot be
   assigned within those limits.

The original random symbol, side, and activation time remain unchanged. The
expiry contract is selected to satisfy IBKR capacity, and quantity may be
reduced by the margin-reserve check below.

## Margin Reserve

Before transmitting each missing order, Agent 0 uses IBKR's native what-if
order preview. It does not estimate futures margin from local constants.

Agent 0 first previews the planned random quantity. A quantity is acceptable
only when the preview reports that post-order equity with loan value exceeds
post-order initial margin by at least 10% of post-order equity with loan value:

`equityWithLoanAfter - initMarginAfter >= 0.10 * equityWithLoanAfter`

If the planned quantity breaches that reserve, Agent 0 previews successively
smaller quantities and submits the largest quantity that satisfies it. If
quantity 1 does not satisfy the reserve, submission stops without transmitting
that order. That row and every later row remain `planned` for a future run.
This check occurs immediately before each submission so every accepted working
order is included in the next preview's account state.

## Cancel All Orders

`run.py --cancel-all` is a separate, explicit operation. After paper-account
validation and connection, it calls IBKR's native `reqGlobalCancel()`, waits
for cancellation updates, reports the number of working orders before and
after the request, resets locally accepted upcoming rows to `planned`, and
exits without generating or submitting weekly orders.

IBKR global cancellation has session-wide scope and no account parameter. It
therefore cancels every working order visible to the connected TWS or IB
Gateway session, including manual orders and orders created by other API client
IDs or managed accounts. Normal `run.py` execution never invokes it.

## Random Decisions and Sizing

- Choose uniformly from configured instruments whose sizing cap is positive.
- Choose `BUY` or `SELL` independently with 50% probability for every order.
- Choose an integer quantity uniformly from 1 through the selected
  instrument's existing Agent 0 sizing cap.
- Do not skip, flatten, inspect positions, or alter choices based on existing
  exposure.
- Keep the current paper-account-only enforcement and paper port.

## Workflow

`python agents/agent_0/run.py` will:

1. Validate the configured paper account and connect to IBKR.
2. Move locally tracked orders whose activation time has passed from
   `orders/upcoming.csv` to `orders/previous.csv`.
3. Create or resume the deterministic 100-order plan for next week. Remove
   local rows whose deterministic references are outside that current plan so
   an older, larger plan cannot survive a configured order-count reduction.
4. Treat IBKR as authoritative: a local `accepted` row whose reference is not
   currently working at IBKR becomes `planned` again.
5. Resolve the nearest three futures contracts per selected symbol and
   pre-allocate the complete remaining plan under the 15-per-contract-side cap.
6. Reconcile deterministic order references with current IBKR open orders so
   orders that really are still working are not duplicated.
7. Preview each missing order against the 10% margin reserve, reduce quantity
   when needed, and submit accepted previews sequentially.
8. Persist each result immediately.
9. Print a concise accepted/rejected/margin-blocked summary and disconnect.

The optional `--account` override remains available; otherwise the account
comes from `AGENT0_IBKR_ACCOUNT`. Testing and planning use pure functions and
fake broker objects and never transmit orders.

The mutually exclusive `--cancel-all` flag selects cancellation instead of the
weekly queue. It cannot be combined with normal queue execution.

## Order Tracking

The old `logs/`, `state/`, CSV decision log, and daily counter are removed.
The replacement `agents/agent_0/orders/` directory contains:

- `upcoming.csv`: planned or accepted orders whose activation time is still
  in the future.
- `previous.csv`: activated, rejected, or otherwise terminal tracked orders.

Both files record the order reference, activation time, instrument, contract,
side, quantity, IBKR order ID, and last known status. Because the program is
not running during the week, the local files reconcile only when `run.py` is
started again; IBKR is authoritative between runs.

## Failure and Duplicate Handling

- Order references are deterministic for the target date and daily sequence.
- A rerun treats a matching IBKR open order as already submitted.
- A locally accepted matching reference is skipped only when that reference is
  still present among current IBKR working orders. Otherwise it is reset to
  `planned` and resubmitted.
- The allocation preflight fails before new submission when fewer than three
  valid expiries or existing account orders leave insufficient capacity.
- Every real submission is preceded by an IBKR what-if preview; the planned
  quantity is reduced to the largest positive integer that preserves the 10%
  reserve.
- If quantity 1 breaches the reserve, no rejected order is intentionally sent;
  submission stops and the unsubmitted rows remain `planned`.
- Submission stops on `Cancelled`, `Inactive`, an explicit API error, or a
  connection failure; accepted earlier orders remain tracked.
- If no instrument has a positive sizing cap, the run fails before placing any
  order.
- Global cancellation is reachable only through the explicit `--cancel-all`
  path and never falls through into weekly plan generation.

## Code Shape

- `run.py` becomes the single weekly-queue orchestrator and capacity allocator.
- `random_policy.py` generates entry-only random decisions and activation
  times.
- `orders.py` builds scheduled IBKR orders and reads/writes the two tracking
  CSV files.
- `config.py`, `models.py`, `broker.py`, and `SETTINGS.md` retain only settings
  and behavior needed by the weekly workflow.
- Obsolete `state.py` and `risk_limits.py` are deleted.

## Verification

Tests must demonstrate, before implementation, that:

- the next calendar week contains exactly five dates;
- a plan contains 20 orders per date and 100 total;
- reconciliation removes locally tracked rows outside the current 100-order
  plan while preserving matching rows;
- all activation times fall inside the configured window;
- sides use only `BUY` and `SELL`, with the seeded generator demonstrating
  both outcomes;
- quantities stay inside their selected sizing caps;
- IBKR orders contain `MKT`, `DAY`, account, deterministic reference, and
  `GoodAfterTime` fields;
- tracking moves past activations from upcoming to previous;
- reruns skip matching accepted/open order references;
- locally accepted references absent from IBKR are reset and requeued;
- no contract/side bucket is allocated more than 15 working orders;
- existing non-Agent-0 working orders reduce available bucket capacity;
- insufficient three-expiry capacity fails before any new order is submitted;
- an order quantity is reduced when its what-if preview breaches the 10%
  margin reserve;
- a quantity-1 margin failure stops submission and leaves the remaining rows
  planned without transmitting the blocked order;
- `--cancel-all` invokes one global cancellation, resets accepted local rows,
  waits for IBKR updates, and does not invoke weekly queue generation;
- a rejected submission stops the queue;
- no test connects to or transmits an order to IBKR.
