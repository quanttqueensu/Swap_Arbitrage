# Swap Arbitrage

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
```

Repository organization:

- `agents/agent_0/`, `agents/agent_1/`, and later agent folders contain each
  agent's runtime files and agent-specific tests.
- `data_pipeline/historical_data/` contains FRED/CME source building and canonicalization;
  its historical builder also fetches and assembles source datasets.
  `data_pipeline/live_data_pipeline/` contains IBKR paper-data recording.
- Root strategy stages are named by purpose: `signal_pipeline.py`,
  `risk_pipeline.py`, `backtest_engine.py`, `clean_data.py`, and `config.py`.
- `docs/` contains tests, audit tooling, research/project documentation, and
  historical conversation artifacts. Runtime code does not import from it.
- `data/` contains only the durable raw-data, rates, futures, market, and
  contract-risk folders.

The test suite and import smoke check are offline: they do not contact IBKR,
Cloudflare/R2, or public market-data endpoints. Agent 0's lazy IBKR client is
loaded only to confirm that its installed class is available; no broker
connection is made.
