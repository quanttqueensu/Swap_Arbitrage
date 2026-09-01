# Swap Arbitrage

Python research and paper-trading system for a 2Y/5Y swap spread strategy using Eris SOFR swap futures and Treasury futures.

The strategy compares the Eris-implied swap rate against the corresponding Treasury rate, builds a rolling z-score of the spread, and uses that signal to size hedged positions. Risk is managed using DV01-based sizing and portfolio limits.

## Setup

Python 3.12 is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Running the strategy

Build the signal data:

```powershell
python signal_pipeline.py
```

Build the risk and position-sizing data:

```powershell
python risk_pipeline.py
```

Refresh the underlying Treasury and Eris data if needed:

```powershell
python signal_pipeline.py --treasury --eris
```

## Backtesting

Run the historical backtest with:

```powershell
python -m backtesting --start auto --end auto
```

Backtest results are saved under:

```text
data/results/backtests/
```

## Project structure

```text
signal_pipeline.py        Builds swap-spread signals and target directions
risk_pipeline.py          Handles DV01 sizing and portfolio risk limits
config.py                 Main strategy and data settings
clean_data.py             Cleans derived datasets

data_pipeline/            Historical and live market-data pipelines
backtesting/              Historical backtest engine
agents/                   Paper-trading agents
data/                     Raw data and generated results
docs/                     Tests, documentation, and research notes
```

The current strategy trades the 2Y and 5Y maturities:

```text
2Y: YIT / ZT
5Y: YIW / ZF
```

Signals use a 252-day rolling window with configurable entry and exit z-score thresholds. Position sizes are then adjusted for signal strength and realized volatility before being converted into contract quantities using each leg's DV01.

Most strategy parameters can be changed in `config.py`.

## Tests

```powershell
python -m unittest discover -s docs/tests -v
python -m unittest discover -s agents/agent_0/tests -v
```

For a more detailed breakdown of the architecture and data flow, see:

```text
docs/TECHNICAL_DOCUMENTATION.md
```
