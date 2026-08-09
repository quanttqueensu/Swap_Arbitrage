import csv
from dataclasses import fields
import hashlib
from io import StringIO
from pathlib import Path
import re

from strategy import OrderIntent, SignalDecision, to_csv_row

from .engine import BacktestResult, DailyRecord, FillRecord, PositionRecord, TradeRecord


_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _header(record_type):
    return [field.name for field in fields(record_type)]


def _validate_unique(rows, keys, name):
    identities = [tuple(row[key] for key in keys) for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError(f"{name} contains duplicate keys")


def _render(header: list[str], rows: list[dict[str, str]]) -> bytes:
    if any(list(row) != header for row in rows):
        raise ValueError("row does not match its schema")
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=header, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_results(result: BacktestResult, output_root: Path) -> Path:
    if type(result) is not BacktestResult:
        raise TypeError("result must be a BacktestResult")
    if not isinstance(output_root, Path):
        raise TypeError("output_root must be a Path")
    if not _SAFE_RUN_ID.fullmatch(result.run_id) or result.run_id in {".", ".."}:
        raise ValueError("run_id is not path safe")

    run_dir = output_root / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    daily = [to_csv_row(row) for row in result.daily]
    decisions = [to_csv_row(row) for row in result.decisions]
    orders = [to_csv_row(row) for row in result.orders]
    fills = [to_csv_row(row) for row in result.fills]
    trades = [to_csv_row(row) for row in result.trades]
    positions = [to_csv_row(row) for row in result.positions]
    summary = [{"key": key, "value": value} for key, value in result.summary]

    _validate_unique(daily, ("observation_date",), "daily")
    _validate_unique(decisions, ("decision_id",), "decisions")
    _validate_unique(orders, ("decision_id", "instrument_id"), "orders")
    _validate_unique(fills, ("order_id", "fill_time_utc"), "fills")
    _validate_unique(trades, ("trade_id",), "trades")
    _validate_unique(positions, ("timestamp_utc", "instrument_id"), "positions")
    _validate_unique(summary, ("key",), "summary")

    reports = (
        ("daily.csv", _header(DailyRecord), daily),
        ("decisions.csv", _header(SignalDecision), decisions),
        ("orders.csv", _header(OrderIntent), orders),
        ("fills.csv", _header(FillRecord), fills),
        ("trades.csv", _header(TradeRecord), trades),
        ("positions.csv", _header(PositionRecord), positions),
        ("summary.csv", ["key", "value"], summary),
    )
    contents = {
        filename: _render(header, rows)
        for filename, header, rows in reports
    }
    manifest_entries = (
        *result.manifest,
        *(
            (f"{filename.removesuffix('.csv')}_sha256", hashlib.sha256(content).hexdigest())
            for filename, content in contents.items()
        ),
    )
    manifest_entries = (
        *manifest_entries,
        ("manifest_row_count", str(len(manifest_entries) + 1)),
    )
    manifest = [
        {"key": key, "value": value}
        for key, value in manifest_entries
    ]
    _validate_unique(manifest, ("key",), "manifest")
    contents = {"manifest.csv": _render(["key", "value"], manifest), **contents}
    for filename, content in contents.items():
        _write(run_dir / filename, content)
    return run_dir
