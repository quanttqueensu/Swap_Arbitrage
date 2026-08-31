# Swap Arbitrage

Start with [Technical Documentation](docs/TECHNICAL_DOCUMENTATION.md) for the
paper-only boundary, architecture, verified commands, and contributor runbook.

## Python environment

Python 3.12 is the supported version for this repository. From PowerShell at
the repository root, create and prepare an isolated environment:

```powershell
& "C:\\Path\\To\\Python312\\python.exe" -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install -r requirements.txt
```

The canonical test command is:

```powershell
python -m unittest discover -s docs/tests -v
python -m unittest discover -s agents/agent_0/tests -v
python -m unittest discover -s agents/agent_1/tests -v
```

Repository organization:

- `agents/agent_0/`, `agents/agent_1/`, and later agent folders contain each
  agent's runtime files and agent-specific tests.
- `data_pipeline/historical_data/` contains FRED/CME source building and canonicalization;
  its historical builder also fetches and assembles source datasets.
  `data_pipeline/live_data_pipeline/` contains IBKR paper-data recording.
- Root strategy stages are named by purpose: `signal_pipeline.py`,
  `risk_pipeline.py`, `clean_data.py`, and `config.py`. The `backtesting/`
  package runs the canonical historical replay.
- `docs/` contains tests, audit tooling, research/project documentation, and
  historical conversation artifacts. Runtime code does not import from it.
- `data/` contains durable raw-data, rates, futures, market, and contract-risk
  folders; `data/results/backtests/` contains canonical historical-backtest
  report sets.

## Historical backtesting

The supported historical backtest command is:

```powershell
python -m backtesting --start auto --end auto
```

Each run writes the validated canonical report set under
`data/results/backtests/<run-id>/`. Use `--refresh-signals` only when the
upstream signal/risk data should be rebuilt.

## Agent 1 paper trader

Agent 1 reconciles the validated 2Y and 5Y strategy targets with an IBKR paper
account. Set `PAPER_ACCOUNT` in `agents/agent_1/config.py` to the local
`DU...` paper account. Start with the read-only status command:

```powershell
python -m agents.agent_1.run status
```

The complete operator command set is:

```powershell
python -m agents.agent_1.run status
python -m agents.agent_1.run delayed-status
python -m agents.agent_1.run once
python -m agents.agent_1.run run
python -m agents.agent_1.run delayed-once
python -m agents.agent_1.run delayed-run
python -m agents.agent_1.run stop-and-flatten
```

By default, `status`, `once`, and `run` refresh the exact ERIS contract
reference/risk data from the public Eris daily files, refresh the 2YY/5YY
historical baseline from IBKR continuous futures, and generate the live target
before Agent 1 evaluates any order. Generated files are cached under
`data/raw_data/cache/` and `data/live_signal/`; no market-data CSV requires
manual maintenance.

`delayed-status` is a read-only diagnostic for accounts without real-time
market-data subscriptions. It requests IBKR delayed data before collecting
quotes, never creates an execution engine, and does not make `once` or `run`
eligible to trade on delayed quotes.

`delayed-once` and `delayed-run` are the paper-only delayed execution path.
They request IBKR delayed market data and inherently use the pre-generated
`--target` CSV, so they never generate the automatic 2YY/5YY-dependent live
target. The target and its contract-risk file still must pass the normal
freshness and risk checks.

Agent 1 fails closed when its target, contract risk, quotes, account state, or
paper configuration is invalid or stale. A persistent operator stop state lives
at `data/paper/agent_1/STOP` by default. `stop-and-flatten` creates that file
before connecting to IBKR; `status`, `once`, and every `run` cycle honor it by
blocking expansion and targeting zero exposure. Remove the stop file only after
an operator has intentionally approved resuming normal paper operation. Use
`--stop-file` to override the private stop-state location.

Offline tests do not establish TWS or IB Gateway connectivity and do not replace
the controlled paper-account acceptance exercises in the Agent 1 design.
