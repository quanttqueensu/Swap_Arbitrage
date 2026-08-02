"""Offline, deterministic staging for the approved canonical migration."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from data_pipeline.canonicalize import (
    SourceTiming,
    canonicalize_daily_market,
    canonicalize_futures,
    canonicalize_rates,
)
from data_pipeline.contracts import MIGRATION_RULES, SCHEMAS, validate_csv
from data_pipeline.manifests import FileManifest, sha256_file, write_input_manifest


REPORT_COLUMNS = (
    "rule_id", "action", "source_paths", "source_sha256", "source_key_count", "staged_destination",
    "output_sha256", "output_key_count", "start_date", "end_date", "schema_version",
    "validation_status", "recovery_path", "timing_rule_id", "timing_matrix_digest", "timing_certainty", "scope",
    "expected_key_digest", "actual_key_digest", "expected_value_digest", "actual_value_digest",
    "spot_evidence", "status", "detail",
)
_SUPPORTED_RULES = frozenset({"cme_swap_master", "treasury_futures_master", "swap_rates", "treasury_futures", "treasury_rates"})
_TIMING_RULE_ID = "p24-market-close-v1"
_TIMING_CERTAINTY = "assumed"
_TIMING_MATRIX = (
    ("ERIS", "2000-01-01", "2099-12-31", "21:00:00Z", 60, "exact", ""),
    ("YAHOO", "2000-01-01", "2099-12-31", "21:00:00Z", 60, "proxy", "continuous futures proxy"),
)
_TIMING_MATRIX_DIGEST = hashlib.sha256(
    json.dumps(_TIMING_MATRIX, separators=(",", ":")).encode("utf-8")
).hexdigest()
_TIMING = {
    "ERIS": (SourceTiming(date(2000, 1, 1), date(2099, 12, 31), time(21, tzinfo=timezone.utc), timedelta(minutes=1), "ERIS", "exact"),),
    "YAHOO": (SourceTiming(date(2000, 1, 1), date(2099, 12, 31), time(21, tzinfo=timezone.utc), timedelta(minutes=1), "YAHOO", "proxy", "continuous futures proxy"),),
}


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationResult:
    repo_root: Path
    staging_root: Path
    report_path: Path
    output_hashes: dict[str, str]
    all_passed: bool


@dataclass(frozen=True)
class _LineageRecord:
    schema_id: str
    destination: str
    key: tuple[str, ...]
    values: tuple[str, ...]
    source: tuple[tuple[str, str], ...] = ()


def require_contained(root: Path, candidate: Path) -> Path:
    """Resolve a new or existing child, rejecting every symlink component."""
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise MigrationError(f"repository root is unavailable: {root}") from error
    if root.is_symlink() or not resolved_root.is_dir():
        raise MigrationError(f"repository root must be a non-symlink directory: {root}")
    absolute = candidate.absolute()
    try:
        relative = absolute.relative_to(root.absolute())
    except ValueError as error:
        raise MigrationError(f"path escapes repository: {candidate}") from error
    current = root.absolute()
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise MigrationError(f"symlink path is not allowed: {current}")
    resolved = candidate.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise MigrationError(f"path escapes repository: {candidate}")
    return resolved


def _repository(root: Path) -> Path:
    root = root.absolute()
    if root.is_symlink() or not root.is_dir() or not (root / ".git").exists() or (root / ".git").is_symlink():
        raise MigrationError(f"repository root requires .git: {root}")
    # Retain the caller's spelling here.  Windows may expose a temporary path
    # once in 8.3 form and once in long form; comparing a child to a resolved
    # parent would incorrectly reject that same directory.
    return root


def _report_destination(repo: Path, report: Path, staging: Path) -> Path:
    """Reports are evidence, never a writable escape hatch into data."""
    destination = require_contained(repo, report)
    verification = require_contained(repo, repo / "docs" / "verification")
    try:
        relative = destination.absolute().relative_to(verification.absolute())
    except ValueError as error:
        raise MigrationError("report must be under docs/verification") from error
    if not relative.parts or destination == staging or staging in destination.parents:
        raise MigrationError("report overlaps staging")
    data = require_contained(repo, repo / "data")
    if destination == data or data in destination.parents:
        raise MigrationError("report overlaps data")
    return destination


def _discover(repo: Path) -> dict[str, Path]:
    discovered: dict[str, Path] = {}
    data = require_contained(repo, repo / "data")
    if not data.exists():
        raise MigrationError("repository has no data directory")
    for path in sorted(data.rglob("*.csv")):
        # ``data`` has already been resolved by the containment guard, so use
        # its resolved parent for a representation-stable Windows relative path.
        relative = path.relative_to(data.parent).as_posix()
        rule_ids = [rule.rule_id for rule in MIGRATION_RULES if rule.matches(relative)]
        if not rule_ids:
            # A staging tree may intentionally live under ``data``.  Only
            # paths selected by the approved migration catalog are inputs.
            continue
        if len(rule_ids) != 1:
            raise MigrationError(f"expected exactly one migration rule for {relative}")
        rule_id = rule_ids[0]
        if rule_id in _SUPPORTED_RULES:
            source = repo / relative
            require_contained(repo, source)
            discovered[rule_id] = source
    required = {"cme_swap_master", "treasury_futures_master", "swap_rates", "treasury_futures", "treasury_rates"}
    missing = sorted(required - set(discovered))
    if missing:
        raise MigrationError(f"missing required supported inputs: {', '.join(missing)}")
    return discovered


def _snapshot_inputs(
    repo: Path,
    sources: dict[str, Path],
    staging: Path,
) -> tuple[dict[str, Path], dict[str, FileManifest], dict[str, str], dict[str, bytes]]:
    """Capture every source once; canonicalizers only ever receive these copies."""
    snapshots = require_contained(staging, staging / ".input-snapshots")
    snapshots.mkdir()
    paths: dict[str, Path] = {}
    manifests: dict[str, FileManifest] = {}
    hashes: dict[str, str] = {}
    captured: dict[str, bytes] = {}
    for rule_id, source in sorted(sources.items()):
        checked = require_contained(repo, source)
        relative = source.absolute().relative_to(repo.absolute()).as_posix()
        before = os.stat(checked, follow_symlinks=False)
        with checked.open("rb") as handle:
            descriptor = os.fstat(handle.fileno())
            if (descriptor.st_dev, descriptor.st_ino) != (before.st_dev, before.st_ino):
                raise MigrationError(f"source changed before snapshot: {relative}")
            data = handle.read()
        after = os.stat(checked, follow_symlinks=False)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise MigrationError(f"source changed during snapshot: {relative}")
        require_contained(repo, source)
        digest = hashlib.sha256(data).hexdigest()
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline="")))
        dates = [row["date"] for row in rows]
        if not rows or not dates:
            raise MigrationError(f"source snapshot is empty: {relative}")
        snapshot = require_contained(staging, snapshots / f"{rule_id}.csv")
        snapshot.write_bytes(data)
        paths[rule_id] = snapshot
        hashes[rule_id] = digest
        captured[rule_id] = data
        manifests[rule_id] = FileManifest(relative, digest, len(rows), min(dates), max(dates), SCHEMAS["run_inputs"].version)
    return paths, manifests, hashes, captured


def _verify_snapshots(repo: Path, sources: dict[str, Path], hashes: dict[str, str]) -> None:
    for rule_id, source in sources.items():
        checked = require_contained(repo, source)
        if sha256_file(checked) != hashes[rule_id]:
            raise MigrationError(f"source changed during staging: {rule_id}")


def _write_csv(root: Path, relative: str, schema_id: str, rows: Iterable[dict[str, str]]) -> Path:
    path = require_contained(root, root / relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [column.name for column in SCHEMAS[schema_id].columns]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    validate_csv(SCHEMAS[schema_id], path)
    return path


def _partitions(root: Path, schema_id: str, pattern: str, partitions: dict[int, list[dict[str, str]]]) -> dict[str, Path]:
    return {pattern.replace("YYYY", str(year)): _write_csv(root, pattern.replace("YYYY", str(year)), schema_id, rows) for year, rows in sorted(partitions.items())}


def _raw_rows(data: bytes, header: list[str], rule_id: str) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    except UnicodeDecodeError as error:
        raise MigrationError(f"{rule_id} snapshot must be UTF-8") from error
    if reader.fieldnames != header:
        raise MigrationError(f"{rule_id} snapshot header differs from independent lineage contract")
    rows = list(reader)
    if not rows:
        raise MigrationError(f"{rule_id} snapshot is empty")
    if any(None in row or any(row[column] is None for column in header) for row in rows):
        raise MigrationError(f"{rule_id} snapshot row width differs from header")
    return rows


def _iso_date(value: str, rule_id: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise MigrationError(f"{rule_id} snapshot date is invalid") from error
    if parsed.isoformat() != value:
        raise MigrationError(f"{rule_id} snapshot date is not canonical")
    return parsed


def _decimal_text(value: str, field: str, rule_id: str, *, scale: Decimal = Decimal("1")) -> str:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise MigrationError(f"{rule_id} {field} is not decimal") from error
    if not parsed.is_finite() or parsed <= 0:
        raise MigrationError(f"{rule_id} {field} must be positive and finite")
    rendered = format(parsed * scale, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _record(
    schema_id: str,
    destination: str,
    row: dict[str, str],
    source: dict[str, str] | None = None,
) -> _LineageRecord:
    contract = SCHEMAS[schema_id]
    columns = tuple(column.name for column in contract.columns)
    if set(row) != set(columns):
        raise MigrationError(f"lineage row does not match {schema_id}")
    key = (schema_id, destination, *(row[column] for column in contract.unique_key))
    return _LineageRecord(
        schema_id,
        destination,
        key,
        tuple(row[column] for column in columns),
        tuple(source.items()) if source is not None else (),
    )


def _independent_market_metadata(source: str, observed: str, rule_id: str) -> tuple[str, str, str, str]:
    day = _iso_date(observed, rule_id)
    matches = [entry for entry in _TIMING_MATRIX if entry[0] == source and date.fromisoformat(entry[1]) <= day <= date.fromisoformat(entry[2])]
    if len(matches) != 1:
        raise MigrationError(f"{rule_id} has no unique independent timing matrix row")
    _, _, _, clock, delay_seconds, classification, proxy_label = matches[0]
    observed_at = datetime.fromisoformat(f"{observed}T{clock[:-1]}+00:00")
    available_at = observed_at + timedelta(seconds=delay_seconds)
    return (
        observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        available_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        classification,
        proxy_label,
    )


def _expected_lineage(rule_id: str, data: bytes) -> list[_LineageRecord]:
    records: list[_LineageRecord] = []
    if rule_id == "treasury_rates":
        header = ["date", "dgs1mo", "dgs2mo", "dgs3mo", "dgs4mo", "dgs6mo", "dgs1", "dgs2", "dgs3", "dgs5", "dgs7", "dgs10", "dgs20", "dgs30", "sofr", "effr"]
        columns = (
            ("dgs2", "UST", "DGS2", "2Y"),
            ("dgs5", "UST", "DGS5", "5Y"),
            ("sofr", "NYFED", "SOFR", "ON"),
            ("effr", "NYFED", "EFFR", "ON"),
        )
        for source_row in _raw_rows(data, header, rule_id):
            observed = source_row["date"]
            year = _iso_date(observed, rule_id).year
            for column, source, series_id, maturity in columns:
                output = {
                    "observation_date": observed,
                    "source": source,
                    "series_id": series_id,
                    "maturity": maturity,
                    "rate_bps": _decimal_text(source_row[column], column, rule_id, scale=Decimal("100")),
                }
                records.append(_record("historical_rates", f"data/source/rates/rates_{year}.csv", output, source_row))
        return records

    if rule_id in {"cme_swap_master", "treasury_futures_master"}:
        for source_row in _raw_rows(data, ["date", "ticker", "price", "dv01"], rule_id):
            observed = source_row["date"]
            year = _iso_date(observed, rule_id).year
            ticker = source_row["ticker"]
            if rule_id == "cme_swap_master":
                months = {"H": "03", "M": "06", "U": "09", "Z": "12"}
                if len(ticker) != 6 or ticker[:3] not in {"YIT", "YIW"} or ticker[3] not in months or not ticker[4:].isdigit():
                    raise MigrationError(f"{rule_id} has an unapproved ticker")
                instrument_id = f"ERIS-{ticker[:3]}-20{ticker[4:]}{months[ticker[3]]}"
                source = "ERIS"
                method = "eris_settlement_dv01"
            else:
                roots = {"ZT=F": "ZT", "ZF=F": "ZF"}
                if ticker not in roots:
                    raise MigrationError(f"{rule_id} has an unapproved ticker")
                instrument_id = f"YAHOO-CONTINUOUS-{roots[ticker]}"
                source = "YAHOO"
                method = "cme_fixed_ics_ratio_proxy"
            settlement = {
                "observation_date": observed,
                "source": source,
                "instrument_id": instrument_id,
                "settlement_price": _decimal_text(source_row["price"], "price", rule_id),
                "dv01_usd_per_bp": "",
            }
            risk = {
                "observation_date": observed,
                "instrument_id": instrument_id,
                "dv01_usd_per_bp": _decimal_text(source_row["dv01"], "dv01", rule_id),
                "rate_sensitivity_sign": "-1",
                "dv01_method": method,
            }
            records.append(_record("historical_futures_settlements", f"data/source/futures/futures_settlements_{year}.csv", settlement, source_row))
            records.append(_record("contract_risk", f"data/canonical/reference/contract_risk_{year}.csv", risk, source_row))
        return records

    market_inputs = {
        "swap_rates": (
            ["date", "eris_swap_2y_price", "eris_swap_2y_return", "eris_swap_5y_price", "eris_swap_5y_return"],
            "ERIS",
            (("eris_swap_2y_price", "ERIS-YIT"), ("eris_swap_5y_price", "ERIS-YIW")),
        ),
        "treasury_futures": (
            ["date", "treasury_futures_2y_price", "treasury_futures_2y_return", "treasury_futures_5y_price", "treasury_futures_5y_return"],
            "YAHOO",
            (("treasury_futures_2y_price", "YAHOO-CONTINUOUS-ZT"), ("treasury_futures_5y_price", "YAHOO-CONTINUOUS-ZF")),
        ),
    }
    if rule_id in market_inputs:
        header, source, columns = market_inputs[rule_id]
        for source_row in _raw_rows(data, header, rule_id):
            observed = source_row["date"]
            year = _iso_date(observed, rule_id).year
            observed_at, available_at, classification, proxy_label = _independent_market_metadata(source, observed, rule_id)
            for column, instrument_id in columns:
                output = {
                    "observation_date": observed,
                    "series_id": "",
                    "instrument_id": instrument_id,
                    "value": _decimal_text(source_row[column], column, rule_id),
                    "value_unit": "price_points",
                    "source_observation_time_utc": observed_at,
                    "available_at_utc": available_at,
                    "source": source,
                    "classification": classification,
                    "proxy_label": proxy_label,
                }
                records.append(_record("daily_market", f"data/canonical/market/daily_market_{year}.csv", output, source_row))
        return records
    raise MigrationError(f"unsupported independent lineage rule: {rule_id}")


def _read_staged_records(files: dict[str, Path], schema_id: str) -> list[_LineageRecord]:
    records: list[_LineageRecord] = []
    for relative, path in sorted(files.items()):
        validate_csv(SCHEMAS[schema_id], path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            records.extend(_record(schema_id, relative, row) for row in csv.DictReader(handle))
    return records


def _record_row(record: _LineageRecord) -> dict[str, str]:
    columns = (column.name for column in SCHEMAS[record.schema_id].columns)
    return dict(zip(columns, record.values, strict=True))


def _actual_lineage(
    rate_files: dict[str, Path],
    settlement_files: dict[str, Path],
    risk_files: dict[str, Path],
    market_files: dict[str, Path],
) -> dict[str, list[_LineageRecord]]:
    rates = _read_staged_records(rate_files, "historical_rates")
    settlements = _read_staged_records(settlement_files, "historical_futures_settlements")
    risks = _read_staged_records(risk_files, "contract_risk")
    market = _read_staged_records(market_files, "daily_market")
    result = {rule_id: [] for rule_id in _SUPPORTED_RULES}
    result["treasury_rates"].extend(rates)
    for record in settlements:
        row = _record_row(record)
        rule_id = "cme_swap_master" if row["source"] == "ERIS" or row["instrument_id"].startswith("ERIS-") else "treasury_futures_master"
        result[rule_id].append(record)
    for record in risks:
        row = _record_row(record)
        rule_id = "cme_swap_master" if row["instrument_id"].startswith("ERIS-") else "treasury_futures_master"
        result[rule_id].append(record)
    for record in market:
        row = _record_row(record)
        rule_id = "swap_rates" if row["source"] == "ERIS" or row["instrument_id"].startswith("ERIS-") else "treasury_futures"
        result[rule_id].append(record)
    return result


def _index_lineage(records: list[_LineageRecord], label: str) -> dict[tuple[str, ...], _LineageRecord]:
    indexed: dict[tuple[str, ...], _LineageRecord] = {}
    for record in records:
        if record.key in indexed:
            raise MigrationError(f"duplicate {label} lineage key: {record.key}")
        indexed[record.key] = record
    return indexed


def _lineage_digest(indexed: dict[tuple[str, ...], _LineageRecord], *, include_values: bool) -> str:
    payload = [
        [list(key), list(indexed[key].values)] if include_values else list(key)
        for key in sorted(indexed)
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()


def _spot_evidence(
    expected: dict[tuple[str, ...], _LineageRecord],
    actual: dict[tuple[str, ...], _LineageRecord],
) -> str:
    ordered = sorted(expected)
    positions = (("first", 0), ("middle", len(ordered) // 2), ("last", len(ordered) - 1))
    spots: list[dict[str, object]] = []
    for position, index in positions:
        record = expected[ordered[index]]
        actual_record = actual.get(record.key)
        spots.append({
            "position": position,
            "source": dict(record.source),
            "expected_output": _record_row(record),
            "actual_output": _record_row(actual_record) if actual_record is not None else None,
        })
    return json.dumps(spots, sort_keys=True, separators=(",", ":"))


def _report_row(
    rule_id: str,
    source: Path,
    source_hash: str,
    outputs: dict[str, Path],
    expected_records: list[_LineageRecord],
    actual_records: list[_LineageRecord],
) -> dict[str, str]:
    rule = next(rule for rule in MIGRATION_RULES if rule.rule_id == rule_id)
    hashes = ";".join(f"{path}:{sha256_file(output)}" for path, output in sorted(outputs.items()))
    expected = _index_lineage(expected_records, f"{rule_id} expected")
    actual = _index_lineage(actual_records, f"{rule_id} actual")
    key_matches = expected.keys() == actual.keys()
    value_matches = key_matches and all(expected[key].values == actual[key].values for key in expected)
    dated_records = actual_records or expected_records
    dates = [_record_row(record)["observation_date"] for record in dated_records]
    return {
        "rule_id": rule_id,
        "action": rule.action,
        "source_paths": source.as_posix(),
        "source_sha256": source_hash,
        "source_key_count": str(len(expected)),
        "staged_destination": ";".join(sorted(outputs)),
        "output_sha256": hashes,
        "output_key_count": str(len(actual)),
        "start_date": min(dates),
        "end_date": max(dates),
        "schema_version": SCHEMAS["run_inputs"].version,
        "validation_status": "pass",
        "recovery_path": rule.recovery,
        "timing_rule_id": _TIMING_RULE_ID,
        "timing_matrix_digest": _TIMING_MATRIX_DIGEST,
        "timing_certainty": _TIMING_CERTAINTY,
        "scope": "5 consumed top-level inputs; 1482 catalog artifacts excluded (including 1474 Eris cache files and r2 inventory)",
        "expected_key_digest": _lineage_digest(expected, include_values=False),
        "actual_key_digest": _lineage_digest(actual, include_values=False),
        "expected_value_digest": _lineage_digest(expected, include_values=True),
        "actual_value_digest": _lineage_digest(actual, include_values=True),
        "spot_evidence": _spot_evidence(expected, actual),
        "status": "pass" if key_matches and value_matches else "fail",
        "detail": f"independent snapshot-to-staged exact reconciliation; keys={'match' if key_matches else 'differ'}; values={'match' if value_matches else 'differ'}",
    }


def _write_report(repo: Path, report: Path, rows: list[dict[str, str]]) -> Path:
    # ``stage_migration`` validates this caller-supplied target before any
    # staging work; retain its resolved spelling for Windows 8.3 stability.
    destination = report
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return destination


def stage_migration(repo_root: Path, staging_root: Path, report_path: Path, *, _shadow: bool = False) -> MigrationResult:
    repo = _repository(repo_root)
    staging = require_contained(repo, staging_root)
    report = _report_destination(repo, report_path, staging)
    if staging.exists() and any(staging.iterdir()):
        raise MigrationError(f"staging root must be empty: {staging}")
    if staging.exists() and staging.is_symlink():
        raise MigrationError(f"symlink path is not allowed: {staging}")
    created_staging = not staging.exists()
    staging.mkdir(parents=True, exist_ok=True)
    try:
        (staging / ".git").mkdir(exist_ok=True)  # enables manifest profiling without publication.
        sources = _discover(repo)
        snapshots, input_manifests, source_hashes, source_bytes = _snapshot_inputs(repo, sources, staging)
        expected_lineage = {
            rule_id: _expected_lineage(rule_id, source_bytes[rule_id])
            for rule_id in sorted(_SUPPORTED_RULES)
        }

        rates = canonicalize_rates(snapshots["treasury_rates"])
        futures = canonicalize_futures(snapshots["cme_swap_master"], snapshots["treasury_futures_master"])
        market = canonicalize_daily_market(snapshots["swap_rates"], snapshots["treasury_futures"], _TIMING)
        rate_files = _partitions(staging, "historical_rates", "data/source/rates/rates_YYYY.csv", rates)
        settlement_files = _partitions(staging, "historical_futures_settlements", "data/source/futures/futures_settlements_YYYY.csv", futures.settlements_by_year)
        risk_files = _partitions(staging, "contract_risk", "data/canonical/reference/contract_risk_YYYY.csv", futures.risk_by_year)
        market_files = _partitions(staging, "daily_market", "data/canonical/market/daily_market_YYYY.csv", market)
        manifest = _write_csv(staging, "data/manifests/p24_inputs.csv", "run_inputs", [])
        write_input_manifest(manifest, "p24-stage", list(input_manifests.values()))
        _verify_snapshots(repo, sources, source_hashes)

        actual_lineage = _actual_lineage(rate_files, settlement_files, risk_files, market_files)
        rows = [
            _report_row("cme_swap_master", sources["cme_swap_master"], source_hashes["cme_swap_master"], {**settlement_files, **risk_files}, expected_lineage["cme_swap_master"], actual_lineage["cme_swap_master"]),
            _report_row("swap_rates", sources["swap_rates"], source_hashes["swap_rates"], market_files, expected_lineage["swap_rates"], actual_lineage["swap_rates"]),
            _report_row("treasury_futures", sources["treasury_futures"], source_hashes["treasury_futures"], market_files, expected_lineage["treasury_futures"], actual_lineage["treasury_futures"]),
            _report_row("treasury_futures_master", sources["treasury_futures_master"], source_hashes["treasury_futures_master"], {**settlement_files, **risk_files}, expected_lineage["treasury_futures_master"], actual_lineage["treasury_futures_master"]),
            _report_row("treasury_rates", sources["treasury_rates"], source_hashes["treasury_rates"], rate_files, expected_lineage["treasury_rates"], actual_lineage["treasury_rates"]),
        ]
        report = _write_report(repo, report, rows)
        _verify_snapshots(repo, sources, source_hashes)
        outputs = [*rate_files, *settlement_files, *risk_files, *market_files, "data/manifests/p24_inputs.csv"]
        hashes = {relative: sha256_file(require_contained(staging, staging / relative)) for relative in sorted(outputs)}
        result = MigrationResult(repo, staging, report, hashes, all(row["status"] == "pass" for row in rows))
        if not _shadow:
            shadow = repo / ".p24-shadow-stage"
            shadow_report = repo / "docs" / "verification" / ".p24-shadow-report.csv"
            if shadow.exists() or shadow_report.exists():
                raise MigrationError("shadow staging evidence path already exists")
            try:
                compared = stage_migration(repo, shadow, shadow_report, _shadow=True)
                if result.output_hashes != compared.output_hashes or report.read_bytes() != shadow_report.read_bytes():
                    raise MigrationError("independent shadow staging bytes differ")
            finally:
                if shadow.exists():
                    shutil.rmtree(shadow)
                shadow_report.unlink(missing_ok=True)
        return result
    except Exception:
        if created_staging:
            shutil.rmtree(staging)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    if args.publish:
        raise MigrationError("publication is reserved for Task 5 and is not implemented")
    result = stage_migration(args.repo_root, args.staging_root, args.report)
    return 0 if result.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
