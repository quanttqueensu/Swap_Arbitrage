from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from config import (
    CME_SWAP_DATA_FILE,
    DV01_VOL_LOOKBACK,
    MATURITIES,
    ERIS_SOFR_SWAP_FUTURES,
    MAX_DV01_SCALE,
    MAX_GROSS_DV01,
    MAX_NET_DV01,
    MAX_SWAP_FUTURES_CONTRACTS,
    MAX_TREASURY_FUTURES_CONTRACTS,
    MIN_DV01_SCALE,
    MIN_TARGET_DV01_TO_TRADE,
    POSITION_SIZE_BY_MATURITY,
    RISK_DATA_FILE,
    SIGNAL_DATA_FILE,
    SWAP_COLUMNS,
    SWAP_DV01_YEARS,
    TREASURY_FUTURES_DATA_FILE,
    TREASURY_DV01_YEARS,
    TREASURY_FUTURES,
)
from clean_data import save_derived_csv, without_dv01_columns
from signal_pipeline import build_signal_data, clean_maturity, clean_signal_frame

DV01_VOL_MIN_PERIODS = max(20, DV01_VOL_LOOKBACK // 3)
MASTER_COLUMNS = ["date", "ticker", "price", "dv01"]


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


def add_risk_budget_for_maturity(df: pd.DataFrame, maturity: str) -> pd.DataFrame:
    output = df.copy()
    m = clean_maturity(maturity)
    position_col = get_position_col(maturity)

    if position_col not in output.columns or POSITION_SIZE_BY_MATURITY.get(maturity, 0) <= 0:
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
    position_size_col = f"position_size_dv01_{m}"
    base_target_dv01 = POSITION_SIZE_BY_MATURITY[maturity]
    active = output[position_col].abs() > 0
    output[position_size_col] = float(base_target_dv01)
    output[target_col] = 0.0
    output.loc[active, target_col] = (
        base_target_dv01 * output.loc[active, vol_scale_col] * output.loc[active, strength_col]
    )
    output[target_col] = output[target_col].clip(0.0, base_target_dv01)
    output.loc[output[target_col] < MIN_TARGET_DV01_TO_TRADE, target_col] = 0.0

    output[f"swap_leg_direction_{m}"] = output[position_col]
    output[f"treasury_leg_direction_{m}"] = -output[position_col]
    return output


def add_contract_sizing_for_maturity(df: pd.DataFrame, maturity: str) -> pd.DataFrame:
    output = df.copy()
    m = clean_maturity(maturity)
    target_col = f"target_dv01_{m}"
    swap_dir_col = f"swap_leg_direction_{m}"

    if target_col not in output.columns or swap_dir_col not in output.columns:
        return output

    swap_dv01_col = f"swap_dv01_per_contract_{m}"
    treasury_dv01_col = f"treasury_dv01_per_contract_{m}"
    reason_col = f"risk_block_reason_{m}"
    output[reason_col] = ""

    if swap_dv01_col not in output.columns:
        output[swap_dv01_col] = np.nan

    if treasury_dv01_col not in output.columns:
        output[treasury_dv01_col] = np.nan

    swap_dv01 = pd.to_numeric(output[swap_dv01_col], errors="coerce")
    output[swap_dv01_col] = swap_dv01.where(swap_dv01 > 0)
    treasury_dv01 = pd.to_numeric(output[treasury_dv01_col], errors="coerce")
    output[treasury_dv01_col] = treasury_dv01.where(treasury_dv01 > 0)

    active = pd.to_numeric(output[target_col], errors="coerce").fillna(0.0) > 0
    missing_swap_dv01 = active & ~pd.to_numeric(output[swap_dv01_col], errors="coerce").gt(0)
    missing_treasury_dv01 = active & ~pd.to_numeric(output[treasury_dv01_col], errors="coerce").gt(0)
    output.loc[missing_swap_dv01, reason_col] = "missing_actual_swap_dv01"
    output.loc[missing_treasury_dv01, reason_col] = "missing_treasury_dv01_proxy"

    tradable_target = output[target_col].where(~(missing_swap_dv01 | missing_treasury_dv01), 0.0)
    swap_dv01 = output[swap_dv01_col]
    swap_float_col = f"swap_futures_contracts_float_{m}"
    swap_rounded_col = f"swap_futures_contracts_rounded_{m}"
    swap_cap_hit_col = f"swap_contract_cap_hit_{m}"

    output[swap_float_col] = (tradable_target / swap_dv01 * output[swap_dir_col]).replace([np.inf, -np.inf], np.nan)

    swap_cap = MAX_SWAP_FUTURES_CONTRACTS.get(maturity, 0)
    output[swap_cap_hit_col] = 0

    if swap_cap > 0:
        output[swap_cap_hit_col] = (output[swap_float_col].abs() > swap_cap).astype(int)
        output[swap_float_col] = output[swap_float_col].clip(-swap_cap, swap_cap)

    output[swap_rounded_col] = output[swap_float_col].round().fillna(0).astype(int)

    signed_swap_col = f"signed_swap_dv01_{m}"
    output[signed_swap_col] = output[swap_rounded_col] * swap_dv01.fillna(0.0)

    contract_notional_col = f"swap_contract_notional_estimate_{m}"
    duration = SWAP_DV01_YEARS.get(maturity, np.nan)
    output[contract_notional_col] = (
        swap_dv01 / dv01_per_1mm(duration) * 1_000_000
        if duration and not pd.isna(duration)
        else np.nan
    )
    output[f"swap_notional_{m}"] = output[swap_rounded_col] * output[contract_notional_col].fillna(0.0)

    futures_dv01 = output[treasury_dv01_col]
    float_col = f"treasury_futures_contracts_float_{m}"
    rounded_col = f"treasury_futures_contracts_rounded_{m}"
    treasury_cap_hit_col = f"treasury_contract_cap_hit_{m}"
    signed_treasury_col = f"signed_treasury_dv01_{m}"
    output[f"treasury_future_symbol_{m}"] = TREASURY_FUTURES.get(maturity, "")
    output[float_col] = (
        -output[signed_swap_col] / futures_dv01
    )

    treasury_cap = MAX_TREASURY_FUTURES_CONTRACTS.get(maturity, 0)
    output[treasury_cap_hit_col] = 0

    if treasury_cap > 0:
        output[treasury_cap_hit_col] = (pd.Series(output[float_col], index=output.index).abs() > treasury_cap).astype(int)
        output[float_col] = pd.Series(output[float_col], index=output.index).clip(-treasury_cap, treasury_cap)

    output[rounded_col] = pd.Series(output[float_col], index=output.index).round().fillna(0).astype(int)
    output[signed_treasury_col] = output[rounded_col] * futures_dv01.fillna(0.0)

    if maturity in TREASURY_DV01_YEARS:
        output[f"treasury_notional_{m}"] = (
            output[signed_treasury_col] / dv01_per_1mm(TREASURY_DV01_YEARS[maturity]) * 1_000_000
        )
    else:
        output[f"treasury_notional_{m}"] = 0.0

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

    for col in target_cols:
        output[col] = output[col] * output["dv01_cap_scale"]

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


def add_portfolio_risk_flags(df: pd.DataFrame) -> pd.DataFrame:
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

    output["gross_swap_dv01_after_rounding"] = output[swap_cols].abs().sum(axis=1) if swap_cols else 0.0
    output["gross_treasury_dv01_after_rounding"] = output[treasury_cols].abs().sum(axis=1) if treasury_cols else 0.0
    output["net_dv01_limit_breach"] = output["net_rate_dv01"].abs().gt(MAX_NET_DV01).astype(int)

    reasons = pd.Series("", index=output.index, dtype="object")

    for maturity in MATURITIES:
        m = clean_maturity(maturity)
        reason_col = f"risk_block_reason_{m}"

        if reason_col not in output.columns:
            continue

        reason = output[reason_col].fillna("").astype(str)
        tagged = pd.Series("", index=output.index, dtype="object")
        has_reason = reason.ne("")
        tagged.loc[has_reason] = f"{m}:" + reason.loc[has_reason]
        both = reasons.ne("") & tagged.ne("")
        empty = reasons.eq("") & tagged.ne("")
        reasons.loc[both] = reasons.loc[both] + "|" + tagged.loc[both]
        reasons.loc[empty] = tagged.loc[empty]

    net_breach = output["net_dv01_limit_breach"].astype(bool)
    both = reasons.ne("") & net_breach
    empty = reasons.eq("") & net_breach
    reasons.loc[both] = reasons.loc[both] + "|portfolio:net_dv01_limit"
    reasons.loc[empty] = "portfolio:net_dv01_limit"

    output["risk_block_reason"] = reasons
    output["risk_allowed"] = reasons.eq("").astype(int)
    return output


def build_risk_columns(signals: pd.DataFrame) -> pd.DataFrame:
    output = signals.copy()

    for maturity in MATURITIES:
        output = add_risk_budget_for_maturity(output, maturity)

    output = enforce_gross_dv01_cap(output)

    for maturity in MATURITIES:
        output = add_contract_sizing_for_maturity(output, maturity)

    return add_portfolio_risk_flags(add_net_dv01(output))


def load_market_master(path: Path, label: str, pull_hint: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}. Run `{pull_hint}` first.")

    output = pd.read_csv(path)

    if output.columns.tolist() != MASTER_COLUMNS:
        raise RuntimeError(f"{label} data must have columns {MASTER_COLUMNS}.")

    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.normalize()
    output["ticker"] = output["ticker"].astype("string").str.strip()
    output["price"] = pd.to_numeric(output["price"], errors="coerce")
    output["dv01"] = pd.to_numeric(output["dv01"], errors="coerce")

    if (
        output[MASTER_COLUMNS].isna().any().any()
        or output["ticker"].eq("").any()
        or (output["price"] <= 0).any()
        or (output["dv01"] <= 0).any()
    ):
        raise RuntimeError(f"{label} data contains missing or invalid values.")

    if output.duplicated(["date", "ticker"]).any():
        raise RuntimeError(f"{label} data contains duplicate date/ticker rows.")

    return output.sort_values(["date", "ticker"]).reset_index(drop=True)


def load_cme_swap_data(path: Path = CME_SWAP_DATA_FILE) -> pd.DataFrame:
    return load_market_master(
        path,
        "CME swap",
        "python -m data_pipeline.historical_data.historical_data_builder --eris",
    )


def load_treasury_futures_data(path: Path = TREASURY_FUTURES_DATA_FILE) -> pd.DataFrame:
    output = load_market_master(
        path,
        "Treasury futures",
        "python -m data_pipeline.historical_data.historical_data_builder --treasury-futures",
    )

    if output["ticker"].str.contains("=F", regex=False).any():
        print(
            "[WARN] Treasury prices/DV01 are continuous-root research proxies; "
            "roll P&L and CTD risk are not production validated."
        )

    return output


def merge_cme_dv01(
    signals: pd.DataFrame,
    master: pd.DataFrame,
    include_tickers: bool = False,
) -> pd.DataFrame:
    output = signals.copy()
    master = master.copy()
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.normalize()
    master["date"] = pd.to_datetime(master["date"], errors="coerce").dt.normalize()

    for maturity, root in ERIS_SOFR_SWAP_FUTURES.items():
        maturity_rows = master[master["ticker"].str.startswith(root)]

        if maturity_rows["date"].duplicated().any():
            raise RuntimeError(
                f"Multiple {maturity} CME contracts selected on one date."
            )

        column = f"swap_dv01_per_contract_{clean_maturity(maturity)}"
        selected_columns = ["date", "dv01"]
        renamed_columns = {"dv01": column}

        if include_tickers:
            selected_columns.append("ticker")
            selected_columns.append("price")
            renamed_columns["ticker"] = f"swap_ticker_{clean_maturity(maturity)}"
            renamed_columns["price"] = f"swap_price_{clean_maturity(maturity)}"

        output = output.merge(
            maturity_rows[selected_columns].rename(columns=renamed_columns),
            on="date",
            how="left",
        )

    return output


def merge_treasury_futures_data(
    signals: pd.DataFrame,
    master: pd.DataFrame,
    include_market_data: bool = False,
) -> pd.DataFrame:
    output = signals.copy()
    master = master.copy()
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.normalize()
    master["date"] = pd.to_datetime(master["date"], errors="coerce").dt.normalize()

    for maturity, root in TREASURY_FUTURES.items():
        maturity_rows = master[master["ticker"].str.startswith(root)]

        if maturity_rows["date"].duplicated().any():
            raise RuntimeError(
                f"Multiple {maturity} Treasury futures rows selected on one date."
            )

        m = clean_maturity(maturity)
        selected_columns = ["date", "dv01"]
        renamed_columns = {"dv01": f"treasury_dv01_per_contract_{m}"}

        if include_market_data:
            selected_columns.extend(["ticker", "price"])
            renamed_columns["ticker"] = f"treasury_ticker_{m}"
            renamed_columns["price"] = f"treasury_price_{m}"

        output = output.merge(
            maturity_rows[selected_columns].rename(columns=renamed_columns),
            on="date",
            how="left",
        )

    return output


def load_signal_or_build(
    refresh_signals: bool = False,
    refresh_raw: bool = False,
    pull_interest_rates: bool = False,
    pull_eris: bool = False,
) -> pd.DataFrame:
    if refresh_signals or refresh_raw or pull_interest_rates or pull_eris or not SIGNAL_DATA_FILE.exists():
        return build_signal_data(
            refresh_raw=refresh_raw,
            pull_interest_rates=pull_interest_rates,
            pull_eris=pull_eris,
            save=True,
        )

    return clean_signal_frame(pd.read_csv(SIGNAL_DATA_FILE))


def build_risk_data(
    refresh_signals: bool = False,
    refresh_raw: bool = False,
    pull_interest_rates: bool = False,
    pull_eris: bool = False,
    save: bool = True,
) -> pd.DataFrame:
    signals = load_signal_or_build(
        refresh_signals=refresh_signals,
        refresh_raw=refresh_raw,
        pull_interest_rates=pull_interest_rates,
        pull_eris=pull_eris,
    )
    output = merge_cme_dv01(signals, load_cme_swap_data())
    output = merge_treasury_futures_data(output, load_treasury_futures_data())
    output = build_risk_columns(output)
    output = without_dv01_columns(output)

    if save:
        save_derived_csv(output, RISK_DATA_FILE)
        print(f"[SAVED] {RISK_DATA_FILE}")

    print(
        "[RISK] "
        f"position_size_dv01={POSITION_SIZE_BY_MATURITY}, "
        f"max_gross_dv01={MAX_GROSS_DV01}, max_net_dv01={MAX_NET_DV01}"
    )
    print(f"[RISK DATA] rows={len(output):,} range={output['date'].min().date()} to {output['date'].max().date()}")
    return output


def self_check() -> None:
    m = "2y"
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
            "proxy_position_2y": [0, 1, 1],
            "eris_swap_2y_price_residual_vs_treasury": [0.0, 1.0, 2.0],
            "eris_swap_2y_price_residual_z": [0.0, 2.0, 2.0],
            "swap_dv01_per_contract_2y": [np.nan, 19.0, 20.0],
            "treasury_dv01_per_contract_2y": [np.nan, 38.0, 40.0],
        }
    )

    checked = build_risk_columns(df)
    target = checked.loc[1, f"target_dv01_{m}"]
    expected_contracts = round(target / 19.0)
    assert target > 0
    assert checked.loc[1, f"swap_futures_contracts_rounded_{m}"] == expected_contracts
    assert np.isclose(checked.loc[1, f"signed_swap_dv01_{m}"], expected_contracts * 19.0)
    assert checked.loc[1, "risk_allowed"] == 1

    missing = df.drop(columns=["swap_dv01_per_contract_2y"])
    blocked = build_risk_columns(missing)
    assert "missing_actual_swap_dv01" in blocked.loc[1, "risk_block_reason"]
    assert blocked.loc[1, f"swap_futures_contracts_rounded_{m}"] == 0

    print("[OK] self-check passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build risk management data from signal data.")
    parser.add_argument("--refresh-signals", action="store_true", help="Rebuild signal pipeline output before risk sizing.")
    parser.add_argument("--refresh-raw", action="store_true", help="Rebuild historical source data before signals/risk.")
    parser.add_argument(
        "--treasury",
        "--interest-rates",
        "--interest_rates",
        "--pull-treasury",
        dest="interest_rates",
        action="store_true",
        help="Refresh Treasury curve and NY Fed rate data before risk sizing.",
    )
    parser.add_argument(
        "--eris",
        "--pull-swaps",
        dest="eris",
        action="store_true",
        help="Refresh public Eris settlement data before risk sizing.",
    )
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.self_check:
        self_check()
        return

    build_risk_data(
        refresh_signals=args.refresh_signals,
        refresh_raw=args.refresh_raw,
        pull_interest_rates=args.interest_rates,
        pull_eris=args.eris,
    )


if __name__ == "__main__":
    main()
