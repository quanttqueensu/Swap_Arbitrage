import csv
from decimal import Decimal
import hashlib
from io import StringIO
import json
from pathlib import Path
import re

from data_pipeline.contracts import SCHEMAS, validate_csv
from strategy import OrderSide, to_csv_row

from .engine import BacktestResult


_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SCHEMA_BY_FILE = {
    "daily.csv": "backtest_daily",
    "decisions.csv": "backtest_decisions",
    "orders.csv": "backtest_orders",
    "fills.csv": "backtest_fills",
    "trades.csv": "backtest_trades",
    "positions.csv": "backtest_positions",
    "summary.csv": "backtest_summary",
}


def _header(schema_id: str) -> list[str]:
    return [column.name for column in SCHEMAS[schema_id].columns]


def _utc(value) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _config_hash(result: BacktestResult, configuration_version: str) -> str:
    manifest = dict(result.manifest)
    keys = (
        "mode",
        "maturity_scope",
        "window_policy",
        "bid_ask_half_spread_points",
        "commission_usd_per_contract",
        "slippage_points",
        "financing_usd_per_contract_day",
        "roll_usd_per_contract",
    )
    config = {
        "configuration_version": configuration_version,
        **{key: manifest[key] for key in keys},
    }
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


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


def _write(path: Path, content: bytes, schema_id: str | None = None) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(content)
        if schema_id is not None:
            validate_csv(SCHEMAS[schema_id], temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _decision_rows(result: BacktestResult) -> list[dict[str, str]]:
    rows = []
    for decision in result.decisions:
        signal = next(
            (value for value in decision.feature_values if value.name == "z_score"),
            None,
        )
        rows.append({
            "decision_id": decision.decision_id,
            "timestamp_utc": _utc(decision.decision_time_utc),
            "strategy_version": decision.strategy_version,
            "config_hash": _config_hash(result, decision.configuration_version),
            "maturity": decision.maturity,
            "prior_state": str(decision.prior_state.value),
            "new_state": str(decision.new_state.value),
            "direction": str(decision.direction.value),
            "reason_code": decision.reason_code,
            "signal_value": "" if signal is None else _decimal(signal.value),
            "signal_unit": "" if signal is None else signal.unit,
        })
    return sorted(rows, key=lambda row: (row["timestamp_utc"], row["decision_id"]))


def _order_rows(result: BacktestResult) -> list[dict[str, str]]:
    decision_times = {
        decision.decision_id: decision.decision_time_utc
        for decision in result.decisions
    }
    statuses = {}
    for fill in result.fills:
        statuses[fill.order_id] = fill.status
    rows = []
    for index, order in enumerate(result.orders, 1):
        order_ref = f"order-{index}"
        quantity = order.quantity_contracts if order.side is OrderSide.BUY else -order.quantity_contracts
        rows.append({
            "order_ref": order_ref,
            "decision_id": order.decision_id,
            "created_at_utc": _utc(decision_times.get(order.decision_id, order.earliest_submission_utc)),
            "instrument_id": order.instrument_id,
            "side": order.side.value,
            "quantity": str(quantity),
            "order_type": order.order_type.value,
            "time_in_force": order.time_in_force.value,
            "status": statuses.get(order_ref, "working"),
        })
    return sorted(rows, key=lambda row: (row["created_at_utc"], row["order_ref"]))


def _fill_rows(result: BacktestResult) -> list[dict[str, str]]:
    commission = Decimal(dict(result.manifest)["commission_usd_per_contract"])
    rows = []
    for fill in result.fills:
        if not fill.filled_quantity_contracts:
            continue
        quantity = (
            fill.filled_quantity_contracts
            if fill.side == OrderSide.BUY.value
            else -fill.filled_quantity_contracts
        )
        rows.append({
            "fill_id": f"fill-{len(rows) + 1}",
            "order_ref": fill.order_id,
            "fill_time_utc": _utc(fill.fill_time_utc),
            "instrument_id": fill.instrument_id,
            "side": fill.side,
            "quantity": str(quantity),
            "fill_price": _decimal(fill.execution_price_points),
            "commission_usd": _decimal(Decimal(fill.filled_quantity_contracts) * commission),
        })
    return sorted(rows, key=lambda row: (row["fill_time_utc"], row["fill_id"]))


def _position_rows(result: BacktestResult) -> list[dict[str, str]]:
    rows = []
    for position in result.positions:
        quantity = Decimal(position.quantity_contracts)
        market_value = quantity * position.mark_price_points * position.multiplier_usd_per_point
        unrealized = quantity * (
            position.mark_price_points - position.average_cost_points
        ) * position.multiplier_usd_per_point
        rows.append({
            "observation_date": position.timestamp_utc.date().isoformat(),
            "instrument_id": position.instrument_id,
            "quantity": str(position.quantity_contracts),
            "market_price": _decimal(position.mark_price_points),
            "market_value_usd": _decimal(market_value),
            "unrealized_pnl_usd": _decimal(unrealized),
            "realized_pnl_usd": _decimal(position.realized_pnl_usd),
        })
    return sorted(rows, key=lambda row: (row["observation_date"], row["instrument_id"]))


def _summary_rows(result: BacktestResult) -> list[dict[str, str]]:
    manifest = dict(result.manifest)
    summary = dict(result.summary)
    return [{
        "run_id": result.run_id,
        "strategy_version": manifest.get("strategy_version") or "unobserved",
        "start_date": summary["start_date"],
        "end_date": summary["end_date"],
        "row_count": str(len(result.daily)),
        "trade_count": str(len(result.trades)),
        "net_pnl_usd": _decimal(sum((row.net_pnl_usd for row in result.daily), Decimal("0"))),
        "ending_equity_usd": summary["ending_equity_usd"],
        "max_drawdown_usd": summary["max_drawdown_usd"],
        "max_drawdown_pct": _decimal(max((row.drawdown_pct for row in result.daily), default=Decimal("0"))),
    }]


def write_results(result: BacktestResult, output_root: Path) -> Path:
    if type(result) is not BacktestResult:
        raise TypeError("result must be a BacktestResult")
    if not isinstance(output_root, Path):
        raise TypeError("output_root must be a Path")
    if not _SAFE_RUN_ID.fullmatch(result.run_id) or result.run_id in {".", ".."}:
        raise ValueError("run_id is not path safe")

    run_dir = output_root / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    rows_by_file = {
        "daily.csv": [to_csv_row(row) for row in result.daily],
        "decisions.csv": _decision_rows(result),
        "orders.csv": _order_rows(result),
        "fills.csv": _fill_rows(result),
        "trades.csv": sorted(
            (to_csv_row(row) for row in result.trades),
            key=lambda row: (row["opened_at_utc"], row["trade_id"]),
        ),
        "positions.csv": _position_rows(result),
        "summary.csv": _summary_rows(result),
    }
    contents = {
        filename: _render(_header(schema_id), rows_by_file[filename])
        for filename, schema_id in _SCHEMA_BY_FILE.items()
    }

    manifest_values = dict(result.manifest)
    for filename, rows in rows_by_file.items():
        manifest_values[f"{filename.removesuffix('.csv')}_row_count"] = str(len(rows))
    manifest_entries = (
        *manifest_values.items(),
        *(
            (f"{filename.removesuffix('.csv')}_sha256", hashlib.sha256(content).hexdigest())
            for filename, content in contents.items()
        ),
    )
    manifest_entries = (
        *manifest_entries,
        ("manifest_row_count", str(len(manifest_entries) + 1)),
    )
    manifest = [{"key": key, "value": value} for key, value in manifest_entries]
    _validate_unique(manifest, ("key",), "manifest")

    _write(run_dir / "manifest.csv", _render(["key", "value"], manifest))
    for filename, schema_id in _SCHEMA_BY_FILE.items():
        _write(run_dir / filename, contents[filename], schema_id)
    return run_dir
