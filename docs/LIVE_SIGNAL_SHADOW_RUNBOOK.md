# Agent 1 Auto-Refreshed Live Signal Runbook

Agent 1 observes YIT, YIW, 2YY, and 5YY through the configured IBKR paper
session. `shadow-once` cannot publish an executable target or call order
execution. `status`, `once`, and `run` use the same inputs for the executable
paper target.

## Prerequisites

- A valid `AGENT1_PAPER_CONFIG` pointing to a DU paper account configuration.
- Paper TWS or IB Gateway on the configured localhost paper port.
- Market-data permissions for YIT, YIW, 2YY, and 5YY.
- Market-data permissions for IBKR continuous historical 2YY and 5YY bars.
- Outbound HTTPS access to `files.erisfutures.com`.

Agent 1 creates and refreshes the baseline, exact-contract ERIS reference, and
execution contract-risk files automatically. The public ERIS file supplies the
contract coupon, B, C, PV01, and DV01. IBKR supplies the continuous historical
2YY/5YY closes. CME's configured fixed inter-commodity ratios convert refreshed
ERIS DV01 into the ZT/ZF hedge-risk proxy.

## One-cycle check

```powershell
python -m agents.agent_1.run shadow-once --run-id shadow-acceptance
```

The command writes:

- `data/paper/agent_1/shadow-acceptance/live_signals.csv`
- `data/paper/agent_1/live_signal_state.json`

Successful output begins with `mode=shadow-only` and
`executable_target_changed=False`. Inspect both maturity rows for contract IDs,
rate/spread units, model values, reason codes, and hypothetical quantities.
Any stale, crossed, missing, mismatched, or invalid input must produce a
specific blocked reason and zero affected exposure.

## Paper execution

```powershell
python -m agents.agent_1.run status
python -m agents.agent_1.run once
python -m agents.agent_1.run run
```

The first command may take longer while the trailing ERIS history is cached.
Subsequent starts reuse the cache and still refresh the current business day.
If ERIS or IBKR data is unavailable, Agent 1 rejects the target and submits no
new exposure.

After the cycle, confirm the paper account has no order attributable to the
shadow command. This manual check complements the offline test proving the
command never creates the Agent 1 execution store.
