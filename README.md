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
account. Copy `agents/agent_1/agent1.paper.example.json` to an untracked private
path, replace the placeholder with the local `DU...` paper account, and point
`AGENT1_PAPER_CONFIG` at that file. Start with the read-only status command:

```powershell
python -m agents.agent_1.run status
```

The complete operator command set is:

```powershell
python -m agents.agent_1.run status
python -m agents.agent_1.run once
python -m agents.agent_1.run run
python -m agents.agent_1.run shadow-once
python -m agents.agent_1.run stop-and-flatten
```

By default, `status`, `once`, and `run` refresh the exact ERIS contract
reference/risk data from the public Eris daily files, refresh the 2YY/5YY
historical baseline from IBKR continuous futures, and generate the live target
before Agent 1 evaluates any order. Generated files are cached under
`data/raw_data/cache/` and `data/live_signal/`; no market-data CSV requires
manual maintenance. Use `--legacy-target` only to deliberately run the old
pre-generated `--target` CSV path.

Agent 1 fails closed when its automatic refresh, target, contract risk, quotes,
account state, or paper configuration is invalid or stale. Offline tests do not
establish TWS or IB Gateway connectivity and do not replace the controlled
paper-account acceptance exercises in the Agent 1 design.

`shadow-once` runs the same automatic refresh and live-signal calculation but
stops before the execution path. The optional `--shadow-config` argument keeps
the original file-backed acceptance fixture available for controlled tests.
Follow [the live-signal runbook](docs/LIVE_SIGNAL_SHADOW_RUNBOOK.md).
