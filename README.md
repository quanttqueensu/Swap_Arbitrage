# Swap Arbitrage

## Python environment

Python 3.12 is the supported version for this repository. From PowerShell at
the repository root, create and prepare an isolated environment:

```powershell
& "C:\\Path\\To\\Python312\\python.exe" -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The canonical test command is:

```powershell
python -m unittest discover -s tests -v
```

The test suite and import smoke check are offline: they do not contact IBKR,
Cloudflare/R2, or public market-data endpoints. Agent 0's lazy IBKR client is
loaded only to confirm that its installed class is available; no broker
connection is made.
