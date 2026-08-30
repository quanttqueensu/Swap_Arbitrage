from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
from typing import Iterable, Mapping, Any


SHADOW_SIGNAL_COLUMNS = [
    "timestamp_utc",
    "strategy_version",
    "snapshot_id",
    "market_snapshot_hash",
    "maturity",
    "eris_symbol",
    "eris_contract_id",
    "treasury_yield_symbol",
    "treasury_yield_contract_id",
    "eris_bid",
    "eris_ask",
    "eris_mid",
    "eris_fixed_coupon_decimal",
    "eris_b_price_points",
    "eris_c_price_points",
    "eris_pv01_usd_per_bp",
    "eris_par_bid_bps",
    "eris_par_ask_bps",
    "eris_par_mid_bps",
    "treasury_yield_bid_bps",
    "treasury_yield_ask_bps",
    "treasury_yield_mid_bps",
    "live_spread_bid_side_bps",
    "live_spread_ask_side_bps",
    "live_spread_mid_bps",
    "historical_mean_bps",
    "historical_std_bps",
    "z_score",
    "prior_state",
    "resulting_state",
    "hypothetical_swap_quantity",
    "hypothetical_treasury_quantity",
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
        unknown = set(row).difference(SHADOW_SIGNAL_COLUMNS)
        if unknown:
            raise ValueError(f"unknown shadow audit fields: {sorted(unknown)}")
        serialized.append(
            {
                column: _serialize(row.get(column))
                for column in SHADOW_SIGNAL_COLUMNS
            }
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    if not needs_header:
        with path.open(newline="", encoding="utf-8") as handle:
            existing_header = next(csv.reader(handle), [])
        if existing_header != SHADOW_SIGNAL_COLUMNS:
            raise ValueError("existing shadow audit header does not match schema")

    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHADOW_SIGNAL_COLUMNS)
        if needs_header:
            writer.writeheader()
        writer.writerows(serialized)
        handle.flush()
        os.fsync(handle.fileno())
