# Strategy improvement assignments

Work through these assignments in order. Each change must use only information
available on that date, so the backtest and research do not use future data.

## 1. Use CTD-based Treasury DV01

Treasury DV01 is currently based on the swap DV01 and a fixed ratio. This is
not realistic because Treasury futures risk changes when the cheapest-to-deliver
bond or conversion factor changes. We want to use realistic daily DV01 data for
Treasury futures.

1. Research a daily source for the cheapest-to-deliver bond, its cash DV01, and
   its conversion factor. Add these fields to
   `data/futures/futures_settlements_YYYY.csv`, then keep the calculated DV01
   and its method in `data/contract_risk/contract_risk_YYYY.csv`.
2. Update `canonicalize_futures()` and `build_ctd_treasury_futures_data()` in
   the historical data pipeline to accept and preserve this data. Make sure
   `risk_pipeline.py` uses it for Treasury sizing instead of the fixed proxy.
3. Check that `backtesting/historical.py` receives the new DV01 through the
   normal risk-data flow, then add a small CTD-DV01 test in `docs/tests/`.

## 2. Use realistic contract roll costs

The backtest currently applies one flat roll charge per contract. This can
understate the cost of keeping a position through a roll because it ignores the
actual contracts, two-sided trading, bid/ask spreads, slippage, and fees. We
want to model the cost of closing the old contract and opening the new one.

1. Decide which dated roll-cost inputs are available and add them to
   `data/futures/futures_settlements_YYYY.csv`. Include enough information to
   identify the old and new contracts and estimate both sides of the roll.
2. Update `canonicalize_futures()` and the event-building code in
   `backtesting/historical.py` so the roll inputs reach the backtest.
3. Update `ReplayEvent`, `run_backtest()`, and `NaiveAssumptions` in
   `backtesting/` to apply roll costs by leg. Make sure the same expected costs
   are available to `strategy/costs.py` for the opportunity calculation.

## 3. Show gross and net opportunity

A trade can look attractive before costs but lose money after entry, exit,
financing, and roll costs. We want to show both gross and net opportunity so
the strategy only acts on trades that remain attractive after expected costs.

1. Define clear gross and net opportunity values in `strategy/spread.py` and
   connect the cost inputs from `strategy/costs.py`, including the roll costs
   from Assignment 2.
2. Add both values to `SpreadObservation` in `strategy/models.py`. Make sure
   `signal_pipeline.py`, `strategy/signal_generation.py`, and `risk_pipeline.py`
   can pass the values through to signals and sizing.
3. Update the backtest-facing signal decisions to require positive net
   opportunity and rank trades by it. Add a few simple cost and ranking examples
   in `docs/tests/`.

## 4. Test better signal choices

A z-score measures only one kind of dislocation. Different market conditions
may be better described by momentum, funding, volatility, or a combination of
signals. We want to test these options and use the ones that work best.

1. Research signal types such as robust dislocation, percentile, momentum, and
   funding or volatility filters. Add them beside the current z-score work in
   `signal_pipeline.py`.
2. Create named signal profiles in `config.py`, then update
   `strategy/signal_generation.py` so it can use one signal or a combination.
   Make sure the chosen profile also flows through `risk_pipeline.py`.
3. Use `backtesting/historical.py` to compare profiles on the same dates, costs,
   liquidity limits, and risk limits. Keep the winning profile and its version
   in the backtest output, and add causality tests in `docs/tests/`.

## 5. Make position sizing liquidity-aware

Current historical sizing relies mainly on signal strength and volatility. It
can recommend a size that is too large for a thin market or too small for a
liquid one. We want to include liquidity so every target size can be executed.

1. Identify useful daily liquidity measures, such as volume, open interest,
   bid/ask spread, and executable depth. Add them to
   `data/futures/futures_settlements_YYYY.csv` and update the data contracts
   and canonicalization code to keep them.
2. Use the new measures in `strategy/position_sizing.py` and `risk_pipeline.py`
   to reduce, cap, or block sizes when markets are too thin. Make sure the
   existing DV01 and portfolio limits still apply after this change.
3. Update `backtesting/historical.py` and `backtesting/engine.py` so simulated
   fills respect the same capacity limits. Test thin, liquid, and missing-data
   examples in `docs/tests/`.

## 6. Research ML feature weights

Manually chosen signal weights are assumptions. A carefully tested model may
find a better combination, but it must prove that result on later unseen data.
We want to use ML only after that evidence is available.

1. List the features worth testing: opportunities, signal variants, funding,
   volatility, liquidity, CTD data, and roll costs. Add the usable features to
   `build_signal_columns()` in `signal_pipeline.py`.
2. Create `research/walk_forward.py` to train and compare simple models using
   separate chronological training, validation, and final test periods. Keep a
   gap between periods so forward returns do not leak across the split.
3. Compare model weights with fixed and equal-weight signal combinations in the
   historical backtest. Update `backtesting/historical.py` and
   `backtesting/reports.py` to record the research profile or model ID, then
   check returns, drawdown, turnover, liquidity, hedge quality, and costs.
