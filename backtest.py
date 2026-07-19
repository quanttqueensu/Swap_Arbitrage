from __future__ import annotations

import argparse
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from config import (
    DATA_DIR,
    ERIS_DOLLARS_PER_POINT,
    MATURITIES,
    RISK_DATA_FILE,
    SWAP_DV01_YEARS,
    TREASURY_FUTURES_DOLLARS_PER_POINT,
)
from data_io import save_derived_csv, without_dv01_columns
from risk_data import (
    build_risk_data,
    dv01_per_1mm,
    load_cme_swap_data,
    load_treasury_futures_data,
    merge_cme_dv01,
    merge_treasury_futures_data,
)
from signal_data import clean_maturity

AUTO_DATE = "auto"
INITIAL_EQUITY = 1_000_000.0


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float = INITIAL_EQUITY
    swap_cost_bps: float = 0.0
    treasury_cost_per_contract: float = 0.0
    label: str = ""


def clean_backtest_frame(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()

    if "date" not in output.columns:
        raise RuntimeError("Signal data must contain a date column.")

    output["date"] = pd.to_datetime(output["date"], errors="coerce")
    output = output.dropna(subset=["date"])
    output = output.drop_duplicates(subset=["date"])
    output = output.sort_values("date").reset_index(drop=True)

    text_cols = ["date", "best_proxy_maturity"]
    text_cols.extend(col for col in output.columns if col.startswith("treasury_future_symbol_"))
    text_cols.extend(col for col in output.columns if col.startswith("risk_block_reason"))
    text_cols.extend(col for col in output.columns if col.startswith("swap_ticker_"))
    text_cols.extend(col for col in output.columns if col.startswith("treasury_ticker_"))

    for column in output.columns.difference(text_cols):
        output[column] = pd.to_numeric(output[column], errors="coerce")

    return output


def load_signal_frame(refresh_signals: bool = False) -> pd.DataFrame:
    if refresh_signals:
        risk = build_risk_data(refresh_signals=True, save=False)
    else:
        if not RISK_DATA_FILE.exists():
            raise FileNotFoundError(f"Missing {RISK_DATA_FILE}. Run `python risk_data.py` first.")
        risk = pd.read_csv(RISK_DATA_FILE)

    output = merge_cme_dv01(risk, load_cme_swap_data(), include_tickers=True)
    output = merge_treasury_futures_data(
        output,
        load_treasury_futures_data(),
        include_market_data=True,
    )
    return clean_backtest_frame(output)


def add_maturity_pnl(df: pd.DataFrame, maturity: str, config: BacktestConfig) -> pd.DataFrame:
    output = df.copy()
    m = clean_maturity(maturity)

    position_col = f"proxy_position_{m}"
    swap_dv01_col = f"swap_dv01_per_contract_{m}"
    treasury_dv01_col = f"treasury_dv01_per_contract_{m}"
    swap_contracts_col = f"swap_futures_contracts_rounded_{m}"
    treasury_contracts_col = f"treasury_futures_contracts_rounded_{m}"
    swap_ticker_col = f"swap_ticker_{m}"
    treasury_ticker_col = f"treasury_ticker_{m}"
    swap_price_col = f"swap_price_{m}"
    treasury_price_col = f"treasury_price_{m}"
    prior_position_col = f"prior_position_{m}"
    swap_pnl_col = f"swap_pnl_{m}"
    treasury_pnl_col = f"treasury_pnl_{m}"
    gross_pnl_col = f"gross_pnl_{m}"
    cost_col = f"transaction_cost_{m}"
    net_pnl_col = f"net_pnl_{m}"

    missing = (
        swap_dv01_col not in output.columns
        or treasury_dv01_col not in output.columns
        or swap_contracts_col not in output.columns
        or treasury_contracts_col not in output.columns
        or swap_price_col not in output.columns
        or treasury_price_col not in output.columns
        or maturity not in SWAP_DV01_YEARS
        or maturity not in TREASURY_FUTURES_DOLLARS_PER_POINT
    )

    output[prior_position_col] = output[position_col].shift(1).fillna(0.0) if position_col in output.columns else 0.0

    required_market_fields = [
        (swap_contracts_col, swap_dv01_col, swap_price_col, swap_ticker_col, "swap"),
        (
            treasury_contracts_col,
            treasury_dv01_col,
            treasury_price_col,
            treasury_ticker_col,
            "Treasury",
        ),
    ]

    for contracts_col, dv01_col, price_col, ticker_col, label in required_market_fields:
        if contracts_col not in output:
            continue

        contracts = pd.to_numeric(output[contracts_col], errors="coerce").fillna(0.0)
        active_contracts = contracts.ne(0) | contracts.shift(1).fillna(0.0).ne(0)

        if not active_contracts.any():
            continue

        dv01 = pd.to_numeric(output.get(dv01_col), errors="coerce") if dv01_col in output else pd.Series(np.nan, index=output.index)
        price = pd.to_numeric(output.get(price_col), errors="coerce") if price_col in output else pd.Series(np.nan, index=output.index)
        ticker = output.get(ticker_col, pd.Series(pd.NA, index=output.index)).astype("string").str.strip()
        invalid = active_contracts & (~dv01.gt(0) | ~price.gt(0) | ticker.isna() | ticker.eq(""))

        if invalid.any():
            dates = output.loc[invalid, "date"].dt.date.astype(str).tolist()
            raise RuntimeError(
                f"Nonzero {maturity} {label} contracts require positive master price/DV01 and ticker: {dates}"
            )

    if missing:
        output[f"swap_turnover_{m}"] = 0.0
        output[f"treasury_contract_turnover_{m}"] = 0.0
        output[swap_pnl_col] = 0.0
        output[treasury_pnl_col] = 0.0
        output[gross_pnl_col] = 0.0
        output[cost_col] = 0.0
        output[net_pnl_col] = 0.0
        return output

    swap_notional = (
        output[swap_contracts_col]
        * output[swap_dv01_col]
        / dv01_per_1mm(SWAP_DV01_YEARS[maturity])
        * 1_000_000
    ).fillna(0.0)
    swap_contracts = pd.to_numeric(output[swap_contracts_col], errors="coerce").fillna(0.0)
    treasury_contracts = pd.to_numeric(output[treasury_contracts_col], errors="coerce").fillna(0.0)
    swap_change = pd.to_numeric(output[swap_price_col], errors="coerce").diff().fillna(0.0)
    treasury_change = pd.to_numeric(output[treasury_price_col], errors="coerce").diff().fillna(0.0)
    swap_roll = pd.Series(False, index=output.index)
    treasury_roll = pd.Series(False, index=output.index)

    if swap_ticker_col in output:
        ticker = output[swap_ticker_col].astype("string").str.strip()
        swap_roll = ticker.notna() & ticker.shift().notna() & ticker.ne(ticker.shift())
        swap_change = swap_change.mask(swap_roll, 0.0)

    if treasury_ticker_col in output:
        ticker = output[treasury_ticker_col].astype("string").str.strip()
        treasury_roll = ticker.notna() & ticker.shift().notna() & ticker.ne(ticker.shift())
        treasury_change = treasury_change.mask(treasury_roll, 0.0)

    output[swap_pnl_col] = swap_contracts.shift(1).fillna(0.0) * ERIS_DOLLARS_PER_POINT * swap_change
    output[treasury_pnl_col] = (
        treasury_contracts.shift(1).fillna(0.0)
        * TREASURY_FUTURES_DOLLARS_PER_POINT[maturity]
        * treasury_change
    )
    output[gross_pnl_col] = output[swap_pnl_col] + output[treasury_pnl_col]
    prior_swap_notional_for_turnover = swap_notional.shift(1).fillna(0.0)
    output[f"swap_turnover_{m}"] = (swap_notional - prior_swap_notional_for_turnover).abs()

    if swap_roll.any():
        output.loc[swap_roll, f"swap_turnover_{m}"] = (
            swap_notional.abs() + prior_swap_notional_for_turnover.abs()
        ).loc[swap_roll]

    prior_treasury_contracts = treasury_contracts.shift(1).fillna(0.0)
    output[f"treasury_contract_turnover_{m}"] = (treasury_contracts - prior_treasury_contracts).abs()

    if treasury_roll.any():
        output.loc[treasury_roll, f"treasury_contract_turnover_{m}"] = (
            treasury_contracts.abs() + prior_treasury_contracts.abs()
        ).loc[treasury_roll]

    output[cost_col] = (
        output[f"swap_turnover_{m}"] * config.swap_cost_bps / 10_000.0
        + output[f"treasury_contract_turnover_{m}"] * config.treasury_cost_per_contract
    )
    output[net_pnl_col] = output[gross_pnl_col] - output[cost_col]
    return output


def add_backtest_pnl(df: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    output = df.copy()

    for maturity in MATURITIES:
        output = add_maturity_pnl(output, maturity, config)

    gross_cols = [col for col in output.columns if col.startswith("gross_pnl_")]
    cost_cols = [col for col in output.columns if col.startswith("transaction_cost_")]
    net_cols = [col for col in output.columns if col.startswith("net_pnl_")]

    output["gross_daily_pnl"] = output[gross_cols].sum(axis=1) if gross_cols else 0.0
    output["transaction_costs"] = output[cost_cols].sum(axis=1) if cost_cols else 0.0
    output["daily_pnl"] = output[net_cols].sum(axis=1) if net_cols else 0.0
    output = add_equity_curve(output, config)
    output = without_dv01_columns(output)
    ticker_columns = [
        column
        for column in output
        if column.startswith("swap_ticker_") or column.startswith("treasury_ticker_")
    ]
    return output.drop(columns=ticker_columns)


def add_equity_curve(df: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    output = df.copy()
    output["equity"] = config.initial_equity + output["daily_pnl"].cumsum()
    output["daily_return"] = output["daily_pnl"] / output["equity"].shift(1).fillna(config.initial_equity)
    peak = output["equity"].cummax().clip(lower=config.initial_equity)
    output["drawdown"] = output["equity"] - peak
    output["drawdown_pct"] = output["drawdown"] / peak
    return output


def resolve_window(df: pd.DataFrame, start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_ts = df["date"].min() if start == AUTO_DATE else pd.to_datetime(start)
    end_ts = df["date"].max() if end == AUTO_DATE else pd.to_datetime(end)

    if pd.isna(start_ts) or pd.isna(end_ts):
        raise RuntimeError(f"Invalid backtest window: start={start}, end={end}")

    if start_ts > end_ts:
        raise RuntimeError(f"Backtest start is after end: {start_ts.date()} > {end_ts.date()}")

    return start_ts, end_ts


def filter_dates(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)


def active_swap_rows(df: pd.DataFrame) -> pd.Series:
    columns = [
        column
        for column in df
        if column.startswith("swap_futures_contracts_rounded_")
    ]

    if not columns:
        return pd.Series(False, index=df.index)

    return df[columns].abs().gt(0).any(axis=1)


def first_active_range(df: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    active = active_swap_rows(df)

    if not active.any():
        return None

    dates = df.loc[active, "date"]
    return dates.min(), dates.max()


def summarize(df: pd.DataFrame, config: BacktestConfig) -> dict[str, float | int | str | None]:
    returns = df["daily_return"].fillna(0.0)
    daily_pnl = df["daily_pnl"].fillna(0.0)
    active = active_swap_rows(df)
    risk_allowed = df.get("risk_allowed", pd.Series(1, index=df.index)).fillna(1)
    vol = returns.std(ddof=0) * math.sqrt(252)
    downside = returns.where(returns < 0, 0.0).std(ddof=0) * math.sqrt(252)
    total_pnl = float(daily_pnl.sum())

    return {
        "start": df["date"].min().date().isoformat(),
        "end": df["date"].max().date().isoformat(),
        "initial_equity": round(float(config.initial_equity), 2),
        "label": config.label or "default",
        "rows": int(len(df)),
        "active_days": int(active.sum()),
        "risk_blocked_days": int((risk_allowed == 0).sum()),
        "pnl_days": int((daily_pnl != 0).sum()),
        "win_days": int((daily_pnl > 0).sum()),
        "loss_days": int((daily_pnl < 0).sum()),
        "gross_pnl": round(float(df["gross_daily_pnl"].sum()), 2),
        "transaction_costs": round(float(df["transaction_costs"].sum()), 2),
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_pnl / config.initial_equity * 100.0, 4),
        "annualized_return_pct": round(float(returns.mean() * 252 * 100.0), 4),
        "annualized_vol_pct": round(float(vol * 100.0), 4),
        "ending_equity": round(float(df["equity"].iloc[-1]), 2),
        "max_drawdown": round(float(df["drawdown"].min()), 2),
        "max_drawdown_pct": round(float(df["drawdown_pct"].min()) * 100.0, 4),
        "best_day": round(float(daily_pnl.max()), 2),
        "worst_day": round(float(daily_pnl.min()), 2),
        "sharpe": None if vol == 0 else round(float(returns.mean() * 252 / vol), 2),
        "sortino": None if downside == 0 else round(float(returns.mean() * 252 / downside), 2),
    }


def print_summary(summary: dict[str, float | int | str | None], output_file) -> None:
    print("\n[BACKTEST SUMMARY]")

    for key, value in summary.items():
        print(f"{key}: {value}")

    print(f"\n[SAVED] {output_file}")


def safe_tag(value: object) -> str:
    return str(value).strip().replace("-", "m").replace(".", "p").replace(" ", "_")


def scenario_suffix(config: BacktestConfig) -> str:
    if config.label:
        return f"_{safe_tag(config.label)}"

    pieces = []

    if not np.isclose(config.initial_equity, INITIAL_EQUITY):
        pieces.append(f"eq{safe_tag(config.initial_equity)}")

    if config.swap_cost_bps:
        pieces.append(f"swap{safe_tag(config.swap_cost_bps)}bps")

    if config.treasury_cost_per_contract:
        pieces.append(f"tsy{safe_tag(config.treasury_cost_per_contract)}")

    return "" if not pieces else f"_{'_'.join(pieces)}"


def output_path(start: pd.Timestamp, end: pd.Timestamp, config: BacktestConfig) -> object:
    stem = f"swap_arb_backtest_{start.date().isoformat()}_{end.date().isoformat()}{scenario_suffix(config)}"
    return DATA_DIR / f"{stem}.csv"


def run_backtest(
    start: str = AUTO_DATE,
    end: str = AUTO_DATE,
    refresh_signals: bool = False,
    config: BacktestConfig = BacktestConfig(),
) -> pd.DataFrame:
    signal_data = load_signal_frame(refresh_signals=refresh_signals)
    full_backtest = add_backtest_pnl(signal_data, config)
    start_ts, end_ts = resolve_window(full_backtest, start=start, end=end)
    backtest = filter_dates(full_backtest, start=start_ts, end=end_ts)

    if backtest.empty:
        raise RuntimeError(f"No rows found from {start} to {end}.")

    backtest = add_equity_curve(backtest, config)

    DATA_DIR.mkdir(exist_ok=True)
    path = output_path(start_ts, end_ts, config)
    backtest = save_derived_csv(backtest, path)

    print_summary(summarize(backtest, config), path)

    if not active_swap_rows(backtest).any():
        active_range = first_active_range(full_backtest)

        if active_range is not None:
            print(f"[WARN] No active risk in selected window. Active signal/risk range: {active_range[0].date()} to {active_range[1].date()}.")

    return backtest


def self_check() -> None:
    df = clean_backtest_frame(
        pd.DataFrame(
            {
                "date": ["2022-01-03", "2022-01-04", "2022-01-05"],
                "swap_price_2y": [100.0, 100.1, 99.9],
                "treasury_price_2y": [102.0, 101.99, 102.0],
                "swap_futures_contracts_rounded_2y": [1, 1, 0],
                "treasury_futures_contracts_rounded_2y": [-1, -1, 0],
                "swap_dv01_per_contract_2y": [19.0, 19.0, 19.0],
                "treasury_dv01_per_contract_2y": [38.0, 38.0, 38.0],
                "swap_ticker_2y": ["YITH24", "YITH24", "YITH24"],
                "treasury_ticker_2y": ["ZT=F", "ZT=F", "ZT=F"],
                "treasury_future_symbol_2y": ["ZT", "ZT", "ZT"],
            }
        )
    )

    checked = add_backtest_pnl(df, BacktestConfig())
    assert np.isclose(checked.loc[1, "gross_daily_pnl"], 120.0)
    assert np.isclose(checked.loc[2, "gross_daily_pnl"], -220.0)
    assert not any("dv01" in column.lower() for column in checked)
    assert checked.loc[0, "treasury_future_symbol_2y"] == "ZT"
    print("[OK] self-check passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one-file swap-arb backtest.")
    parser.add_argument("--start", default=AUTO_DATE, help="YYYY-MM-DD or auto for first available signal-data date.")
    parser.add_argument("--end", default=AUTO_DATE, help="YYYY-MM-DD or auto for last available signal-data date.")
    parser.add_argument("--initial-equity", type=float, default=INITIAL_EQUITY)
    parser.add_argument("--swap-cost-bps", type=float, default=0.0)
    parser.add_argument("--treasury-cost-per-contract", type=float, default=0.0)
    parser.add_argument("--label", default="", help="Optional suffix for scenario output file.")
    parser.add_argument("--refresh-signals", action="store_true", help="Recompute signal and risk data in memory before the run.")
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.self_check:
        self_check()
        return

    config = BacktestConfig(
        initial_equity=args.initial_equity,
        swap_cost_bps=args.swap_cost_bps,
        treasury_cost_per_contract=args.treasury_cost_per_contract,
        label=args.label,
    )
    run_backtest(start=args.start, end=args.end, refresh_signals=args.refresh_signals, config=config)


if __name__ == "__main__":
    main()
