from __future__ import annotations

import numpy as np
import pandas as pd


# Treasury CMT observations are quoted in percent; maturities are in years.
TREASURY_CURVE_NODES = {
    "dgs1mo": 1.0 / 12.0,
    "dgs2mo": 2.0 / 12.0,
    "dgs3mo": 3.0 / 12.0,
    "dgs4mo": 4.0 / 12.0,
    "dgs6mo": 6.0 / 12.0,
    "dgs1": 1.0,
    "dgs2": 2.0,
    "dgs3": 3.0,
    "dgs5": 5.0,
    "dgs7": 7.0,
    "dgs10": 10.0,
    "dgs20": 20.0,
    "dgs30": 30.0,
}


def years_to_maturity(
    observation_dates: pd.Series,
    maturity_dates: pd.Series,
) -> pd.Series:
    observations = pd.to_datetime(observation_dates, errors="coerce").dt.normalize()
    maturities = pd.to_datetime(maturity_dates, errors="coerce").dt.normalize()
    return (maturities - observations).dt.days.astype("float64") / 365.25


def interpolate_treasury_yield_pct(row: pd.Series, target_years: float) -> float:
    """Linearly interpolate a Treasury CMT par yield without extrapolating."""
    if not np.isfinite(target_years) or target_years <= 0:
        return np.nan

    points = []
    for column, maturity_years in TREASURY_CURVE_NODES.items():
        value = pd.to_numeric(row.get(column), errors="coerce")
        if pd.notna(value) and np.isfinite(value):
            points.append((maturity_years, float(value)))

    if len(points) < 2:
        return np.nan

    points.sort()
    maturities, yields = (np.asarray(values, dtype=float) for values in zip(*points))
    if not maturities[0] <= target_years <= maturities[-1]:
        return np.nan
    return float(np.interp(target_years, maturities, yields))


def matched_treasury_yield_bps(
    df: pd.DataFrame,
    maturity_date_col: str,
) -> pd.Series:
    if "date" not in df:
        raise RuntimeError("Yield-curve signal requires a date column.")
    if maturity_date_col not in df:
        raise RuntimeError(
            "Yield-curve signal requires Eris maturity dates. Missing column: "
            f"{maturity_date_col}. Refresh Eris data before enabling "
            "YIELD_CURVE_CONSTRUCTION_SIGNAL."
        )
    if len(TREASURY_CURVE_NODES.keys() & set(df.columns)) < 2:
        raise RuntimeError("Yield-curve signal requires at least two Treasury CMT nodes.")

    target_years = years_to_maturity(df["date"], df[maturity_date_col])
    matched_pct = [
        interpolate_treasury_yield_pct(row, target)
        for (_, row), target in zip(df.iterrows(), target_years)
    ]
    return pd.Series(matched_pct, index=df.index, dtype="float64") * 100.0
