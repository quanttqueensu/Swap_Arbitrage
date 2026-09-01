from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

import pandas as pd

from config import DATA_DIR, MATURITIES, ROLLING_WINDOW, Z_ENTRY, Z_EXIT
from signals.yield_curve_signal import TREASURY_CURVE_NODES


OUTPUT_DIR = DATA_DIR / "live_signal"
TENOR_LABELS = {
    "dgs1mo": "1M",
    "dgs2mo": "2M",
    "dgs3mo": "3M",
    "dgs4mo": "4M",
    "dgs6mo": "6M",
    "dgs1": "1Y",
    "dgs2": "2Y",
    "dgs3": "3Y",
    "dgs5": "5Y",
    "dgs7": "7Y",
    "dgs10": "10Y",
    "dgs20": "20Y",
    "dgs30": "30Y",
}


def _atomic_json(payload: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _save_figure(figure: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        figure.savefig(temporary, format="png", dpi=150)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _pyplot():
    os.environ.setdefault(
        "MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "swap-arbitrage-matplotlib")
    )
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _latest_curve(raw: pd.DataFrame) -> tuple[pd.Timestamp, list[dict[str, object]]]:
    columns = [column for column in TREASURY_CURVE_NODES if column in raw]
    if "date" not in raw or len(columns) < 2:
        raise RuntimeError("Treasury curve snapshot requires a date and at least two nodes")

    values = raw[columns].apply(pd.to_numeric, errors="coerce")
    dates = pd.to_datetime(raw["date"], errors="coerce")
    valid = dates.notna() & values.notna().sum(axis=1).ge(2)
    if not valid.any():
        raise RuntimeError("Treasury curve snapshot has no usable observations")

    index = dates[valid].idxmax()
    nodes = [
        {
            "tenor": TENOR_LABELS[column],
            "years": TREASURY_CURVE_NODES[column],
            "yield_pct": float(values.at[index, column]),
        }
        for column in columns
        if pd.notna(values.at[index, column])
    ]
    return dates.at[index], nodes


def _spread_payload(signals: pd.DataFrame) -> tuple[pd.Timestamp, list[dict[str, object]]]:
    if "date" not in signals:
        raise RuntimeError("Spread snapshot requires a date column")
    dates = pd.to_datetime(signals["date"], errors="coerce")
    spread_columns = [
        f"swap_spread_bps_{maturity.lower()}"
        for maturity in MATURITIES
        if f"swap_spread_bps_{maturity.lower()}" in signals
    ]
    if not spread_columns:
        raise RuntimeError("Spread snapshot has no spread columns")

    numeric = signals[spread_columns].apply(pd.to_numeric, errors="coerce")
    valid = dates.notna() & numeric.notna().any(axis=1)
    if not valid.any():
        raise RuntimeError("Spread snapshot has no usable observations")
    index = dates[valid].idxmax()

    rows = []
    for maturity in MATURITIES:
        key = maturity.lower()
        fields = {
            "eris_rate_bps": f"eris_swap_{key}_equivalent_par_rate_bps",
            "treasury_matched_rate_bps": f"treasury_rate_proxy_bps_{key}",
            "spread_bps": f"swap_spread_bps_{key}",
            "z_score": f"swap_spread_bps_{key}_z",
            "position": f"proxy_position_{key}",
        }
        values = {
            name: pd.to_numeric(signals.at[index, column], errors="coerce")
            if column in signals else None
            for name, column in fields.items()
        }
        if pd.isna(values["spread_bps"]):
            continue
        rows.append(
            {
                "maturity": maturity,
                **{
                    name: None if value is None or pd.isna(value) else float(value)
                    for name, value in values.items()
                },
            }
        )
    return dates.at[index], rows


def publish_market_snapshot(
    raw: pd.DataFrame,
    signals: pd.DataFrame,
    output_dir: Path = OUTPUT_DIR,
) -> tuple[Path, ...]:
    generated = datetime.now(timezone.utc).isoformat()
    curve_date, nodes = _latest_curve(raw)
    spread_date, spreads = _spread_payload(signals)
    output_dir = Path(output_dir)

    curve_json = output_dir / "current_yield_curve.json"
    curve_png = output_dir / "current_yield_curve.png"
    spreads_json = output_dir / "current_spreads.json"
    spreads_png = output_dir / "current_spreads.png"

    _atomic_json(
        {
            "observed_date": curve_date.date().isoformat(),
            "generated_at_utc": generated,
            "source": "US_TREASURY_CMT",
            "signal_model": "yield_curve",
            "nodes": nodes,
        },
        curve_json,
    )
    _atomic_json(
        {
            "observed_date": spread_date.date().isoformat(),
            "generated_at_utc": generated,
            "signal_model": "yield_curve",
            "spreads": spreads,
        },
        spreads_json,
    )

    plt = _pyplot()
    figure, axis = plt.subplots(figsize=(9, 5), constrained_layout=True)
    axis.plot(
        [node["years"] for node in nodes],
        [node["yield_pct"] for node in nodes],
        marker="o",
    )
    axis.set_xscale("log")
    axis.set_xticks(
        [node["years"] for node in nodes],
        [node["tenor"] for node in nodes],
    )
    axis.set_title(f"US Treasury Yield Curve — {curve_date.date().isoformat()}")
    axis.set_xlabel("Maturity")
    axis.set_ylabel("Yield (%)")
    axis.grid(alpha=0.25)
    _save_figure(figure, curve_png)
    plt.close(figure)

    figure, (spread_axis, z_axis) = plt.subplots(
        2, 1, figsize=(9, 7), sharex=True, constrained_layout=True
    )
    dates = pd.to_datetime(signals["date"], errors="coerce")
    for maturity in MATURITIES:
        column = f"swap_spread_bps_{maturity.lower()}"
        if column not in signals:
            continue
        series = pd.to_numeric(signals[column], errors="coerce")
        history = pd.DataFrame({"date": dates, "spread": series}).dropna().tail(
            max(ROLLING_WINDOW * 3, 60)
        )
        if history.empty:
            continue
        current = history.iloc[-1]["spread"]
        line = spread_axis.plot(
            history["date"], history["spread"], label=f"{maturity}: {current:.1f} bps"
        )[0]
        z_column = f"{column}_z"
        if z_column in signals:
            z_history = pd.DataFrame(
                {"date": dates, "z": pd.to_numeric(signals[z_column], errors="coerce")}
            ).dropna().tail(max(ROLLING_WINDOW * 3, 60))
            if not z_history.empty:
                current_z = z_history.iloc[-1]["z"]
                z_axis.plot(
                    z_history["date"], z_history["z"], color=line.get_color(),
                    label=f"{maturity}: z={current_z:.2f}",
                )
    spread_axis.axhline(0, color="black", linewidth=0.8, alpha=0.5)
    spread_axis.set_title(f"Eris - Matched Treasury Spreads — {spread_date.date().isoformat()}")
    spread_axis.set_ylabel("Spread (bps)")
    spread_axis.grid(alpha=0.25)
    spread_axis.legend()
    z_axis.axhline(Z_ENTRY, color="black", linestyle="--", alpha=0.6, label=f"Entry ±{Z_ENTRY}")
    z_axis.axhline(-Z_ENTRY, color="black", linestyle="--", alpha=0.6)
    z_axis.axhline(Z_EXIT, color="black", linestyle=":", alpha=0.5, label=f"Exit ±{Z_EXIT}")
    z_axis.axhline(-Z_EXIT, color="black", linestyle=":", alpha=0.5)
    z_axis.set_xlabel("Date")
    z_axis.set_ylabel("Z-score")
    z_axis.grid(alpha=0.25)
    z_axis.legend()
    _save_figure(figure, spreads_png)
    plt.close(figure)

    return curve_json, curve_png, spreads_json, spreads_png
