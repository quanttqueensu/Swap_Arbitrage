from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from config import (
    MATURITIES,
    RAW_PRICE_DATA_FILE,
    ROLLING_WINDOW,
    SIGNAL_DATA_FILE,
    SWAP_COLUMNS,
    SWAP_EQUIVALENT_PAR_RATE_COLUMNS,
    TREASURY_FUTURES_PRICE_COLUMNS,
    Z_ENTRY,
    Z_EXIT,
)
from clean_data import save_derived_csv
from data_pipeline.historical_data.historical_data_builder import (
    build_raw_price_data,
    clean_price_frame,
    load_csv,
)

MIN_PERIODS = ROLLING_WINDOW


def clean_maturity(maturity: str) -> str:
    return maturity.lower()


def clean_signal_frame(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()

    if "date" not in output.columns:
        raise RuntimeError("Signal data must contain a date column.")

    output["date"] = pd.to_datetime(output["date"], errors="coerce")
    output = output.dropna(subset=["date"])
    output = output.drop_duplicates(subset=["date"])
    output = output.sort_values("date").reset_index(drop=True)

    text_cols = ["date", "best_proxy_maturity"]

    for column in output.columns.difference(text_cols):
        output[column] = pd.to_numeric(output[column], errors="coerce")

    return output


def rolling_zscore(series: pd.Series) -> pd.Series:
    mean = series.rolling(ROLLING_WINDOW, min_periods=MIN_PERIODS).mean()
    std = series.rolling(ROLLING_WINDOW, min_periods=MIN_PERIODS).std()
    return ((series - mean) / std).replace([np.inf, -np.inf], np.nan)


def build_proxy_position(zscore: pd.Series) -> pd.Series:
    positions = []
    state = 0

    for z_value in zscore:
        if pd.isna(z_value):
            state = 0
            positions.append(state)
            continue

        long_entry = z_value <= -Z_ENTRY
        short_entry = z_value >= Z_ENTRY

        if state == 0:
            if long_entry:
                state = 1
            elif short_entry:
                state = -1
        elif state == 1:
            if short_entry:
                state = -1
            elif z_value >= -Z_EXIT:
                state = 0
        elif state == -1:
            if long_entry:
                state = 1
            elif z_value <= Z_EXIT:
                state = 0

        positions.append(state)

    return pd.Series(positions, index=zscore.index)


def rolling_residual(y: pd.Series, x: pd.Series) -> pd.Series:
    x_mean = x.rolling(ROLLING_WINDOW, min_periods=MIN_PERIODS).mean()
    y_mean = y.rolling(ROLLING_WINDOW, min_periods=MIN_PERIODS).mean()
    cov_xy = x.rolling(ROLLING_WINDOW, min_periods=MIN_PERIODS).cov(y)
    var_x = x.rolling(ROLLING_WINDOW, min_periods=MIN_PERIODS).var()
    beta = cov_xy / var_x.replace(0, np.nan)
    alpha = y_mean - beta * x_mean
    return (y - (alpha + beta * x)).replace([np.inf, -np.inf], np.nan)


def add_funding_spread_proxy(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()

    if "effr" in output.columns and "sofr" in output.columns:
        output["funding_spread_proxy_bps"] = (output["effr"] - output["sofr"]) * 100.0
    elif "sofr" in output.columns:
        output["funding_spread_proxy_bps"] = 0.0
    else:
        output["funding_spread_proxy_bps"] = np.nan

    output["funding_spread_proxy_mean_bps"] = (
        output["funding_spread_proxy_bps"].rolling(ROLLING_WINDOW, min_periods=MIN_PERIODS).mean()
    )
    return output


def add_proxy_signal(df: pd.DataFrame, maturity: str) -> tuple[pd.DataFrame, bool]:
    output = df.copy()
    maturity_key = clean_maturity(maturity)
    treasury_col = TREASURY_FUTURES_PRICE_COLUMNS.get(maturity)
    proxy_col = SWAP_COLUMNS.get(maturity)

    if not proxy_col or proxy_col not in output.columns:
        return output, False

    price_z_col = f"{proxy_col}_price_z"
    residual_col = f"{proxy_col}_residual_vs_treasury"
    residual_z_col = f"{proxy_col}_residual_z"
    rate_proxy_col = f"treasury_rate_proxy_bps_{maturity_key}"
    rate_spread_col = f"swap_spread_bps_{maturity_key}"
    rate_spread_z_col = f"{rate_spread_col}_z"
    signal_col = f"proxy_signal_{maturity_key}"
    position_col = f"proxy_position_{maturity_key}"
    dgs_col = {"2Y": "dgs2", "5Y": "dgs5"}.get(maturity)
    equivalent_rate_col = f"eris_swap_{maturity_key}_equivalent_par_rate_bps"

    if dgs_col in output.columns and equivalent_rate_col in output.columns:
        treasury_rate = pd.to_numeric(output[dgs_col], errors="coerce") * 100.0
        equivalent_rate = pd.to_numeric(output[equivalent_rate_col], errors="coerce")
        valid_rate_spread = np.isfinite(treasury_rate) & np.isfinite(equivalent_rate)
        output[rate_proxy_col] = treasury_rate.where(valid_rate_spread)
        output[rate_spread_col] = (equivalent_rate - treasury_rate).where(valid_rate_spread)
    else:
        output[rate_proxy_col] = np.nan
        output[rate_spread_col] = np.nan

    output[rate_spread_z_col] = rolling_zscore(output[rate_spread_col])

    output[price_z_col] = rolling_zscore(output[proxy_col])

    if treasury_col in output.columns:
        output[residual_col] = rolling_residual(y=output[proxy_col], x=output[treasury_col])
        output[residual_z_col] = rolling_zscore(output[residual_col])
    else:
        output[residual_col] = np.nan
        output[residual_z_col] = np.nan

    source = output[rate_spread_z_col]

    output[signal_col] = 0
    output.loc[source <= -Z_ENTRY, signal_col] = 1
    output.loc[source >= Z_ENTRY, signal_col] = -1
    output[position_col] = build_proxy_position(source)

    return output, True


def add_best_maturity_columns(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()
    proxy_z_cols = {
        maturity: f"swap_spread_bps_{clean_maturity(maturity)}_z"
        for maturity in MATURITIES
        if f"swap_spread_bps_{clean_maturity(maturity)}_z" in output.columns
    }

    if not proxy_z_cols:
        return output

    proxy_z_frame = output[list(proxy_z_cols.values())].copy()
    proxy_z_frame.columns = list(proxy_z_cols.keys())
    proxy_abs = proxy_z_frame.abs()
    valid_rows = proxy_abs.notna().any(axis=1)

    output["best_proxy_maturity"] = pd.Series(pd.NA, index=output.index, dtype="object")
    output["best_proxy_abs_z"] = np.nan
    output.loc[valid_rows, "best_proxy_maturity"] = proxy_abs.loc[valid_rows].idxmax(axis=1)
    output.loc[valid_rows, "best_proxy_abs_z"] = proxy_abs.loc[valid_rows].max(axis=1)

    proxy_ranks = proxy_abs.rank(axis=1, ascending=False, method="min")

    for maturity in proxy_z_cols:
        output[f"proxy_rank_{clean_maturity(maturity)}"] = proxy_ranks[maturity]

    return output


def build_signal_columns(raw: pd.DataFrame) -> pd.DataFrame:
    output = clean_price_frame(raw)
    signal_columns = [
        column
        for maturity in MATURITIES
        for column in (
            SWAP_COLUMNS.get(maturity),
            SWAP_EQUIVALENT_PAR_RATE_COLUMNS.get(maturity),
            {"2Y": "dgs2", "5Y": "dgs5"}.get(maturity),
        )
        if column
    ]
    missing = [column for column in signal_columns if column not in output]

    if missing:
        raise RuntimeError(f"Missing daily signal columns: {missing}")

    output = add_funding_spread_proxy(output)
    loaded = []

    for maturity in MATURITIES:
        output, has_signal = add_proxy_signal(output, maturity)

        if has_signal:
            loaded.append(maturity)

    output = add_best_maturity_columns(output)
    print(f"[SIGNALS] proxy maturities: {', '.join(loaded) if loaded else 'none'}")
    return output


def load_raw_or_build(
    refresh_raw: bool = False,
    pull_interest_rates: bool = False,
    pull_eris: bool = False,
) -> pd.DataFrame:
    if refresh_raw or pull_interest_rates or pull_eris or not RAW_PRICE_DATA_FILE.exists():
        return build_raw_price_data(
            refresh_interest_rates=pull_interest_rates,
            refresh_eris=pull_eris,
        )

    return load_csv(RAW_PRICE_DATA_FILE)


def build_signal_data(
    refresh_raw: bool = False,
    pull_interest_rates: bool = False,
    pull_eris: bool = False,
    save: bool = True,
) -> pd.DataFrame:
    raw = load_raw_or_build(
        refresh_raw=refresh_raw,
        pull_interest_rates=pull_interest_rates,
        pull_eris=pull_eris,
    )
    output = build_signal_columns(raw)

    if save:
        output = save_derived_csv(output, SIGNAL_DATA_FILE)
        print(f"[SAVED] {SIGNAL_DATA_FILE}")

    print(f"[SIGNAL DATA] rows={len(output):,} range={output['date'].min().date()} to {output['date'].max().date()}")
    return output


def self_check() -> None:
    positions = build_proxy_position(pd.Series([-3.0, -1.0, 0.0, 3.0, 1.0, 0.0]))
    assert positions.tolist() == [1, 1, 0, -1, -1, 0]

    print("[OK] self-check passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build signal data from consolidated raw data.")
    parser.add_argument("--refresh-raw", action="store_true", help="Rebuild historical source data from captured files.")
    parser.add_argument(
        "--treasury",
        "--interest-rates",
        "--interest_rates",
        "--pull-treasury",
        dest="interest_rates",
        action="store_true",
        help="Refresh Treasury curve and NY Fed rate data before building signals.",
    )
    parser.add_argument(
        "--eris",
        "--pull-swaps",
        dest="eris",
        action="store_true",
        help="Refresh public Eris settlement data before building signals.",
    )
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.self_check:
        self_check()
        return

    build_signal_data(
        refresh_raw=args.refresh_raw,
        pull_interest_rates=args.interest_rates,
        pull_eris=args.eris,
    )


if __name__ == "__main__":
    main()
