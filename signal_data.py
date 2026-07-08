from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from config import (
    DATA_DIR,
    DV01_VOL_LOOKBACK,
    MATURITIES,
    MAX_DV01_PER_MATURITY,
    MAX_DV01_SCALE,
    MAX_GROSS_DV01,
    MIN_DV01_SCALE,
    RAW_PRICE_DATA_FILE,
    ROLLING_WINDOW,
    SIGNAL_DATA_FILE,
    SWAP_COLUMNS,
    SWAP_DV01_YEARS,
    TREASURY_COLUMNS,
    TREASURY_DV01_YEARS,
    TREASURY_FUTURES,
    TREASURY_FUTURES_DV01_PER_CONTRACT,
    Z_ENTRY,
    Z_EXIT,
)
from raw_price_data import build_raw_price_data, clean_price_frame, load_csv

MIN_PERIODS = max(20, int(ROLLING_WINDOW * 0.25))
DV01_VOL_MIN_PERIODS = max(20, DV01_VOL_LOOKBACK // 3)


def clean_maturity(maturity: str) -> str:
    return maturity.lower()


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
    treasury_col = TREASURY_COLUMNS.get(maturity)
    proxy_col = SWAP_COLUMNS.get(maturity)

    if not proxy_col or proxy_col not in output.columns:
        return output, False

    price_z_col = f"{proxy_col}_price_z"
    residual_col = f"{proxy_col}_residual_vs_treasury"
    residual_z_col = f"{proxy_col}_residual_z"
    signal_col = f"proxy_signal_{maturity_key}"
    position_col = f"proxy_position_{maturity_key}"

    output[price_z_col] = rolling_zscore(output[proxy_col])

    if treasury_col in output.columns:
        output[residual_col] = rolling_residual(y=output[proxy_col], x=output[treasury_col])
        output[residual_z_col] = rolling_zscore(output[residual_col])
        source = output[residual_z_col]
    else:
        output[residual_col] = np.nan
        output[residual_z_col] = np.nan
        source = output[price_z_col]

    output[signal_col] = 0
    output.loc[source <= -Z_ENTRY, signal_col] = 1
    output.loc[source >= Z_ENTRY, signal_col] = -1
    output[position_col] = build_proxy_position(source)

    return output, True


def add_best_maturity_columns(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()
    proxy_z_cols = {
        maturity: f"{SWAP_COLUMNS[maturity]}_residual_z"
        for maturity in MATURITIES
        if maturity in SWAP_COLUMNS and f"{SWAP_COLUMNS[maturity]}_residual_z" in output.columns
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
    output = add_funding_spread_proxy(output)
    loaded = []

    for maturity in MATURITIES:
        output, has_signal = add_proxy_signal(output, maturity)

        if has_signal:
            loaded.append(maturity)

    output = add_best_maturity_columns(output)
    print(f"[SIGNALS] proxy maturities: {', '.join(loaded) if loaded else 'none'}")
    return output


def dv01_per_1mm(duration_years: float) -> float:
    return 1_000_000 * 0.0001 * duration_years


def get_position_col(maturity: str) -> str:
    return f"proxy_position_{clean_maturity(maturity)}"


def get_z_col(maturity: str) -> str | None:
    proxy_col = SWAP_COLUMNS.get(maturity)
    return f"{proxy_col}_residual_z" if proxy_col else None


def get_vol_source_col(maturity: str) -> str | None:
    proxy_col = SWAP_COLUMNS.get(maturity)
    return f"{proxy_col}_residual_vs_treasury" if proxy_col else None


def rolling_realized_vol(series: pd.Series) -> pd.Series:
    return (
        series.diff()
        .rolling(DV01_VOL_LOOKBACK, min_periods=DV01_VOL_MIN_PERIODS)
        .std()
        .mul(np.sqrt(252))
        .replace([np.inf, -np.inf], np.nan)
    )


def vol_scale_from_series(series: pd.Series) -> pd.Series:
    vol = rolling_realized_vol(series)
    median_vol = vol.rolling(DV01_VOL_LOOKBACK, min_periods=DV01_VOL_MIN_PERIODS).median()
    return (median_vol / vol).replace([np.inf, -np.inf], np.nan).clip(MIN_DV01_SCALE, MAX_DV01_SCALE).fillna(1.0)


def add_sizing_for_maturity(df: pd.DataFrame, maturity: str) -> pd.DataFrame:
    output = df.copy()
    m = clean_maturity(maturity)
    position_col = get_position_col(maturity)

    if (
        position_col not in output.columns
        or maturity not in SWAP_DV01_YEARS
        or maturity not in TREASURY_DV01_YEARS
        or MAX_DV01_PER_MATURITY.get(maturity, 0) <= 0
    ):
        return output

    output[position_col] = pd.to_numeric(output[position_col], errors="coerce").fillna(0).astype(int)

    vol_source_col = get_vol_source_col(maturity)
    vol_col = f"realized_vol_{m}"
    vol_scale_col = f"dv01_vol_scale_{m}"

    if vol_source_col and vol_source_col in output.columns:
        output[vol_col] = rolling_realized_vol(output[vol_source_col])
        output[vol_scale_col] = vol_scale_from_series(output[vol_source_col])
    else:
        output[vol_col] = np.nan
        output[vol_scale_col] = 1.0

    z_col = get_z_col(maturity)
    strength_col = f"signal_strength_scale_{m}"
    output[strength_col] = (
        (output[z_col].abs() / 2.0).clip(0.0, 1.0)
        if z_col and z_col in output.columns
        else 1.0
    )

    target_col = f"target_dv01_{m}"
    base_target_dv01 = MAX_DV01_PER_MATURITY[maturity]
    active = output[position_col].abs() > 0
    output[target_col] = 0.0
    output.loc[active, target_col] = (
        base_target_dv01 * output.loc[active, vol_scale_col] * output.loc[active, strength_col]
    )
    output[target_col] = output[target_col].clip(0.0, base_target_dv01)

    swap_dir_col = f"swap_leg_direction_{m}"
    treasury_dir_col = f"treasury_leg_direction_{m}"
    output[swap_dir_col] = output[position_col]
    output[treasury_dir_col] = -output[position_col]

    output[f"signed_swap_dv01_{m}"] = output[target_col] * output[swap_dir_col]
    output[f"signed_treasury_dv01_{m}"] = output[target_col] * output[treasury_dir_col]
    output[f"swap_notional_{m}"] = output[target_col] / dv01_per_1mm(SWAP_DV01_YEARS[maturity]) * 1_000_000 * output[swap_dir_col]
    output[f"treasury_notional_{m}"] = output[target_col] / dv01_per_1mm(TREASURY_DV01_YEARS[maturity]) * 1_000_000 * output[treasury_dir_col]

    futures_dv01 = TREASURY_FUTURES_DV01_PER_CONTRACT.get(maturity, np.nan)
    float_col = f"treasury_futures_contracts_float_{m}"
    output[f"treasury_future_symbol_{m}"] = TREASURY_FUTURES.get(maturity, "")
    output[float_col] = output[target_col] / futures_dv01 * output[treasury_dir_col]
    output[f"treasury_futures_contracts_rounded_{m}"] = output[float_col].round().fillna(0).astype(int)

    return output


def enforce_gross_dv01_cap(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()
    target_cols = [
        f"target_dv01_{clean_maturity(maturity)}"
        for maturity in MATURITIES
        if f"target_dv01_{clean_maturity(maturity)}" in output.columns
    ]

    if not target_cols:
        output["gross_dv01_before_cap"] = 0.0
        output["dv01_cap_scale"] = 1.0
        output["gross_dv01_after_cap"] = 0.0
        return output

    output["gross_dv01_before_cap"] = output[target_cols].abs().sum(axis=1)
    output["dv01_cap_scale"] = 1.0
    too_large = output["gross_dv01_before_cap"] > MAX_GROSS_DV01
    output.loc[too_large, "dv01_cap_scale"] = MAX_GROSS_DV01 / output.loc[too_large, "gross_dv01_before_cap"]

    for maturity in MATURITIES:
        m = clean_maturity(maturity)

        for prefix in [
            "target_dv01",
            "signed_swap_dv01",
            "signed_treasury_dv01",
            "swap_notional",
            "treasury_notional",
            "treasury_futures_contracts_float",
        ]:
            col = f"{prefix}_{m}"

            if col in output.columns:
                output[col] = output[col] * output["dv01_cap_scale"]

        rounded_col = f"treasury_futures_contracts_rounded_{m}"
        float_col = f"treasury_futures_contracts_float_{m}"

        if rounded_col in output.columns and float_col in output.columns:
            output[rounded_col] = output[float_col].round().fillna(0).astype(int)

    output["gross_dv01_after_cap"] = output[target_cols].abs().sum(axis=1)
    return output


def add_net_dv01(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()
    swap_cols = [
        f"signed_swap_dv01_{clean_maturity(maturity)}"
        for maturity in MATURITIES
        if f"signed_swap_dv01_{clean_maturity(maturity)}" in output.columns
    ]
    treasury_cols = [
        f"signed_treasury_dv01_{clean_maturity(maturity)}"
        for maturity in MATURITIES
        if f"signed_treasury_dv01_{clean_maturity(maturity)}" in output.columns
    ]

    output["total_signed_swap_dv01"] = output[swap_cols].sum(axis=1) if swap_cols else 0.0
    output["total_signed_treasury_dv01"] = output[treasury_cols].sum(axis=1) if treasury_cols else 0.0
    output["net_rate_dv01"] = output["total_signed_swap_dv01"] + output["total_signed_treasury_dv01"]
    return output


def build_risk_columns(signals: pd.DataFrame) -> pd.DataFrame:
    output = signals.copy()

    for maturity in MATURITIES:
        output = add_sizing_for_maturity(output, maturity)

    return add_net_dv01(enforce_gross_dv01_cap(output))


def load_raw_or_build(refresh_raw: bool = False, pull_treasury: bool = False, pull_swaps: bool = False) -> pd.DataFrame:
    if refresh_raw or pull_treasury or pull_swaps or not RAW_PRICE_DATA_FILE.exists():
        return build_raw_price_data(refresh_treasury=pull_treasury, refresh_swaps=pull_swaps)

    return load_csv(RAW_PRICE_DATA_FILE)


def build_signal_data(
    refresh_raw: bool = False,
    pull_treasury: bool = False,
    pull_swaps: bool = False,
    save: bool = True,
) -> pd.DataFrame:
    raw = load_raw_or_build(refresh_raw=refresh_raw, pull_treasury=pull_treasury, pull_swaps=pull_swaps)
    signals = build_signal_columns(raw)
    output = build_risk_columns(signals)

    if save:
        DATA_DIR.mkdir(exist_ok=True)
        output.to_csv(SIGNAL_DATA_FILE, index=False)
        print(f"[SAVED] {SIGNAL_DATA_FILE}")

    print(f"[SIGNAL DATA] rows={len(output):,} range={output['date'].min().date()} to {output['date'].max().date()}")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build consolidated signal and risk data.")
    parser.add_argument("--refresh-raw", action="store_true", help="Rebuild raw_price_data.csv from captured files.")
    parser.add_argument("--pull-treasury", action="store_true", help="Refresh Treasury and NY Fed data.")
    parser.add_argument("--pull-swaps", action="store_true", help="Refresh Eris swap futures from IBKR.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_signal_data(
        refresh_raw=args.refresh_raw,
        pull_treasury=args.pull_treasury,
        pull_swaps=args.pull_swaps,
    )


if __name__ == "__main__":
    main()
