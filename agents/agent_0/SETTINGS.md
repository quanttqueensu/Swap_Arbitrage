# Agent 0 Settings

Agent 0 queues random paper-market orders for IBKR to activate during the next
calendar week. It does not use Swap-Arb signals or current positions.

## IBKR Routing

- Paper only: `True`
- Live trading enabled: `False`
- Host: `127.0.0.1`
- Port: `7497`
- Client ID: `30`
- Required account environment variable: `AGENT0_IBKR_ACCOUNT`
- Required paper-account prefix: `DU`

Agent 0 refuses non-paper account settings.

## Weekly Orders

- Target: next calendar week's Monday through Friday
- Orders per day: `5` (`25` total across five weekdays)
- Activation: random from `09:00` through `15:00 America/New_York`
- Instruments: random positive-cap contract from `YIT`, `YIW`, `ZT`, and `ZF`
- Side: independent 50% `BUY`, 50% `SELL`
- Quantity: random integer from `1` through the selected instrument's cap
- Margin: IBKR what-if preview preserves at least 10% of post-order equity
  above post-order initial margin; quantity is reduced when necessary
- Order: transmitted `MKT` with `DAY` time in force and IBKR `GoodAfterTime`
- Contracts: the nearest three expiries at least 14 days from expiry
- Working-order limit: at most 15 orders per contract and side
- Preflight: the whole remaining week must fit before any new order is sent
- Skips: none
- Flattening: none
- Position checks: none

Market orders execute at the price available when IBKR activates them; the
execution price is not guaranteed.

If quantity 1 would breach the reserve, Agent 0 stops without transmitting
that order and leaves it and all later orders planned for a future run.

## Sizing

The cap is 10% of the main strategy's historical absolute maximum contract
quantity, with a one-contract minimum when the main quantity is positive. The
current sizing file provides Eris swap-future and Treasury-future contract
counts. Older files can fall back to the configured swap-notional estimate.

## Order Tracking

Agent 0 uses only:

- `orders/upcoming.csv` for planned and accepted future orders
- `orders/previous.csv` for activated or rejected orders

Deterministic order references and IBKR open-order reconciliation let a partial
run resume without resubmitting orders that are still working at IBKR. IBKR is
authoritative: a locally accepted order that is no longer returned by IBKR is
reset to planned and submitted again. The CSV files refresh only when `run.py`
runs.

## Run

Set the paper account once in PowerShell:

```powershell
$env:AGENT0_IBKR_ACCOUNT = "YOUR_PAPER_ACCOUNT"
```

With Trader Workstation or IB Gateway connected, queue next week's orders:

```powershell
.venv\Scripts\python.exe agents\agent_0\run.py
```

Cancel every working order visible to the connected TWS or IB Gateway session,
including manual orders and orders from other API clients or managed accounts:

```powershell
.venv\Scripts\python.exe agents\agent_0\run.py --cancel-all
```

The cancellation command resets local upcoming rows to planned and exits. It
does not generate or submit a weekly plan.

After the accepted summary prints, Python and Trader Workstation may close.
IBKR holds the transmitted orders. Local `orders/upcoming.csv` and
`orders/previous.csv` refresh the next time this command runs.
