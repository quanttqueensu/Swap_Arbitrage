# Agent 0 Settings

Agent 0 is a paper-only random trading agent. It is isolated under
`agents/agent_0` and does not use Swap-Arb signals for entries.

## IBKR Routing

- Agent name: `agent_0`
- Paper only: `True`
- Live trading enabled: `False`
- IBKR host: `127.0.0.1`
- IBKR port: `7497`
- IBKR client ID: `30`
- Minimum days to futures expiry: `14`
- Required account environment variable: `AGENT0_IBKR_ACCOUNT`
- Required paper account prefix: `DU`

The agent refuses to start unless `AGENT0_IBKR_ACCOUNT` is set and the value
looks like an IBKR paper account.

## Instrument Universe

Agent 0 uses the same configured futures universe as the main strategy:

- Eris SOFR swap futures: `YIT`, `YIW`
- Treasury futures: `ZT`, `ZF`, `ZN`, `ZB`

## Trading Rules

- Entries are random only.
- Signal-based entries are disabled.
- Flattening is allowed.
- Max trades per day: `5`
- Max order size: `10%` of the main strategy's historical absolute max
  quantity cap.
- Minimum order quantity: `1` when the main-strategy quantity is positive.
- Order type: `MKT`
- Time in force: `DAY`

The current main sizing file provides Treasury futures contract counts and
swap-leg notional. Agent 0 uses historical absolute max sizing from that file,
not today's signal, so entry timing and direction remain random. For Eris swap
futures, Agent 0 converts swap notional to an estimated contract cap using
`$1,000,000` notional per futures contract.

## Random Policy

- Skip weight: `0.35`
- Entry weight: `0.35`
- Flatten weight: `0.30`

Flattening is only eligible when Agent 0's configured paper account has an
allowed open position.

## Run Modes

- One decision now: `python -m agents.agent_0.run --account DUQ346848`
- Continuous loop: `python -m agents.agent_0.run --loop --account DUQ346848`
- Daily batch: `python -m agents.agent_0.run --batch --account DUQ346848`

Batch mode uses the remaining daily trade slots immediately. It removes the
random skip choice for that run, so each slot attempts an entry or eligible
flatten unless a risk check blocks it.
