from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path

import pandas as pd

from strategy.live_signal import (
    DailySpreadObservation,
    HistoricalModelState,
    LIVE_SIGNAL_STRATEGY_VERSION,
)


ROLLING_WINDOW = 252
DEFAULT_SIGNAL_STATE = {"2Y": 0, "5Y": 0}
REQUIRED_BASELINE_COLUMNS = {
    "timestamp_utc",
    "maturity",
    "strategy_version",
    "spread_bps",
}
REQUIRED_OBSERVATION_COLUMNS = REQUIRED_BASELINE_COLUMNS | {
    "eris_rate_bps",
    "fred_series",
    "treasury_rate_bps",
}


def _require_aware(as_of: datetime) -> datetime:
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return as_of.astimezone(timezone.utc)


def load_model_state(path: Path, maturity: str, as_of: datetime) -> HistoricalModelState:
    as_of_utc = _require_aware(as_of)
    if not path.exists():
        raise FileNotFoundError(path)

    frame = pd.read_csv(path)
    missing = REQUIRED_BASELINE_COLUMNS.difference(frame.columns)
    if missing:
        raise RuntimeError(f"baseline missing columns: {sorted(missing)}")

    timestamps = pd.to_datetime(frame["timestamp_utc"], errors="coerce", utc=True)
    spreads = pd.to_numeric(frame["spread_bps"], errors="coerce")
    valid = (
        timestamps.notna()
        & spreads.notna()
        & frame["maturity"].astype(str).eq(maturity)
        & frame["strategy_version"].astype(str).eq(LIVE_SIGNAL_STRATEGY_VERSION)
        & (timestamps <= pd.Timestamp(as_of_utc))
    )

    eligible = pd.DataFrame(
        {"timestamp_utc": timestamps[valid], "spread_bps": spreads[valid]}
    ).sort_values("timestamp_utc")
    eligible = eligible.tail(ROLLING_WINDOW)

    if eligible.empty:
        raise RuntimeError(
            f"no eligible baseline rows for {maturity} {LIVE_SIGNAL_STRATEGY_VERSION}"
        )

    values = eligible["spread_bps"]
    mean = values.mean()
    std = values.std()

    return HistoricalModelState(
        version=LIVE_SIGNAL_STRATEGY_VERSION,
        mean_bps=Decimal(str(mean)),
        std_bps=Decimal(str(std)),
        observation_count=int(len(values)),
    )


def load_daily_observation(
    path: Path,
    maturity: str,
    as_of: datetime,
) -> DailySpreadObservation:
    as_of_utc = _require_aware(as_of)
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path)
    missing = REQUIRED_OBSERVATION_COLUMNS.difference(frame.columns)
    if missing:
        raise RuntimeError(f"baseline missing columns: {sorted(missing)}")

    timestamps = pd.to_datetime(frame["timestamp_utc"], errors="coerce", utc=True)
    valid = (
        timestamps.notna()
        & frame["maturity"].astype(str).eq(maturity)
        & frame["strategy_version"].astype(str).eq(LIVE_SIGNAL_STRATEGY_VERSION)
        & (timestamps <= pd.Timestamp(as_of_utc))
    )
    eligible = frame.loc[valid].copy()
    eligible["timestamp_utc"] = timestamps[valid]
    eligible = eligible.sort_values("timestamp_utc")
    if eligible.empty:
        raise RuntimeError(f"no eligible daily observation for {maturity}")

    row = eligible.iloc[-1]
    try:
        return DailySpreadObservation(
            maturity=maturity,
            observed_at=row["timestamp_utc"].to_pydatetime(),
            eris_rate_bps=Decimal(str(row["eris_rate_bps"])),
            fred_series=str(row["fred_series"]),
            treasury_rate_bps=Decimal(str(row["treasury_rate_bps"])),
            spread_bps=Decimal(str(row["spread_bps"])),
        )
    except Exception as exc:
        raise RuntimeError(f"invalid daily observation for {maturity}") from exc


def load_signal_state(path: Path) -> dict[str, int]:
    if not path.exists():
        return dict(DEFAULT_SIGNAL_STATE)

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("signal state must be a JSON object")

    result = dict(DEFAULT_SIGNAL_STATE)
    for maturity in result:
        value = raw.get(maturity, 0)
        if type(value) is not int or value not in {-1, 0, 1}:
            raise RuntimeError(f"invalid signal state for {maturity}")
        result[maturity] = value
    return result


def save_signal_state(path: Path, state: dict[str, int]) -> None:
    normalized = {}
    for maturity in DEFAULT_SIGNAL_STATE:
        value = state.get(maturity, 0)
        if type(value) is not int or value not in {-1, 0, 1}:
            raise ValueError(f"invalid signal state for {maturity}")
        normalized[maturity] = value

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(normalized, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except (OSError, TypeError, ValueError):
        temporary.unlink(missing_ok=True)
        raise
