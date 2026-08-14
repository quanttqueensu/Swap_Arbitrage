# File map

## Maintained historical backtesting

The only maintained historical workflow is `python -m backtesting`:
`historical_data_builder -> signal_pipeline -> risk_pipeline -> backtesting`.

| Path | Responsibility |
| --- | --- |
| `backtesting/engine.py` | Sole simulation and accounting engine. |
| `backtesting/historical.py` | Adapts existing historical signal/risk output into causal replay events and writes canonical results. |
| `backtesting/reports.py` | Validates and writes the canonical report set. |
| `backtesting/__main__.py` | Provides the single `python -m backtesting` CLI and offline self-check. |
| `docs/tests/test_historical_backtest.py` | Covers historical adaptation, causal timing, roll marks, and CLI behavior. |

Results are written to `data/results/backtests/<run-id>/` as `manifest.csv`,
`daily.csv`, `decisions.csv`, `orders.csv`, `fills.csv`, `trades.csv`,
`positions.csv`, and `summary.csv`.
