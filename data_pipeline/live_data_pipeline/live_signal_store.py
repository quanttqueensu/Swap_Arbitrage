from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
from typing import Iterable, Mapping, Any


LIVE_SIGNAL_COLUMNS = [
    "generated_at_utc",
    "observation_time_utc",
    "strategy_version",
    "snapshot_id",
    "data_snapshot_hash",
    "maturity",
    "eris_symbol",
    "fred_series",
    "eris_rate_bps",
    "treasury_rate_bps",
    "spread_bps",
    "historical_mean_bps",
    "historical_std_bps",
    "z_score",
    "prior_state",
    "resulting_state",
    "target_swap_quantity",
    "target_treasury_quantity",
    "blocked",
    "reason_codes",
]


def _serialize(value: Any) -> str | int:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (tuple, list)):
        return "|".join(str(item) for item in value)
    return value


def append_rows(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    materialized = list(rows)
    if not materialized:
        return

    serialized = []
    for row in materialized:
        unknown = set(row).difference(LIVE_SIGNAL_COLUMNS)
        if unknown:
            raise ValueError(f"unknown live-signal audit fields: {sorted(unknown)}")
        serialized.append(
            {
                column: _serialize(row.get(column))
                for column in LIVE_SIGNAL_COLUMNS
            }
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    if not needs_header:
        with path.open(newline="", encoding="utf-8") as handle:
            existing_header = next(csv.reader(handle), [])
        if existing_header != LIVE_SIGNAL_COLUMNS:
            raise ValueError("existing live-signal audit header does not match schema")

    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LIVE_SIGNAL_COLUMNS)
        if needs_header:
            writer.writeheader()
        writer.writerows(serialized)
        handle.flush()
        os.fsync(handle.fileno())
