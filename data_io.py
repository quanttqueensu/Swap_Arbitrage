from __future__ import annotations

from pathlib import Path

import pandas as pd


def without_dv01_columns(df: pd.DataFrame) -> pd.DataFrame:
    columns = [column for column in df.columns if "dv01" in column.lower()]
    return df.drop(columns=columns)


def save_derived_csv(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    output = without_dv01_columns(df)
    path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(path, index=False)
    return output


def clean_existing_derived_csvs(
    data_dir: Path,
    master_paths: Path | list[Path],
) -> list[Path]:
    cleaned = []
    protected = [master_paths] if isinstance(master_paths, Path) else master_paths
    protected_paths = {path.resolve() for path in protected}

    for path in sorted(data_dir.glob("*.csv")):
        if path.resolve() in protected_paths:
            continue

        columns = pd.read_csv(path, nrows=0).columns

        if any("dv01" in column.lower() for column in columns):
            save_derived_csv(pd.read_csv(path), path)
            cleaned.append(path)

    return cleaned
