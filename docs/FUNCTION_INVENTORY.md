# Function inventory

## Maintained historical backtesting

Use `python -m backtesting` for the supported historical workflow.

### `backtesting/historical.py`

- `run_historical_backtest(run_id: str, output_root: Path, start: str = "auto", end: str = "auto", refresh_signals: bool = False, assumptions: NaiveAssumptions = NAIVE_ASSUMPTIONS, initial_equity_usd: Decimal = Decimal("1000000"))` → **Output:** `tuple[BacktestResult, Path]`

### `backtesting/__main__.py`

- `parse_args(argv: list[str] | None = None)` → **Output:** `argparse.Namespace`
- `self_check()` → **Output:** `None`
- `main(argv: list[str] | None = None)` → **Output:** `int`

### `backtesting/engine.py`

- `run_backtest(run_id: str, events: object, strategy: Callable[[MarketSnapshot], StrategyResult], assumptions: NaiveAssumptions = NAIVE_ASSUMPTIONS, initial_equity_usd: Decimal = Decimal("1000000"), start_date: date | None = None, end_date: date | None = None)` → **Output:** `BacktestResult`

### `backtesting/reports.py`

- `write_results(result: BacktestResult, output_root: Path)` → **Output:** `Path`
