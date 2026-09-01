# Swap Arbitrage

Swap Arbitrage is a Python research and paper-trading project for exploring relative-value opportunities between 2-year and 5-year Eris swap futures and the U.S. Treasury curve. It collects and normalizes market data, calculates spreads and trading signals, sizes DV01-balanced positions, applies portfolio and risk limits, and replays the strategy through a historical backtesting engine with configurable execution costs.

The repository includes historical and live data pipelines, shared strategy components, reporting tools, and two isolated IBKR paper-trading agents. Agent 1 turns validated strategy targets into supervised paper orders with reconciliation, freshness checks, margin controls, and fail-closed safety behavior, while Agent 0 is a separate constrained random-order experiment. The system is intended for research and paper trading only; its backtests and offline checks do not establish profitability or production readiness.
