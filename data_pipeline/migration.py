"""Offline, deterministic staging for the approved canonical migration."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable

from data_pipeline.canonicalize import (
    SourceTiming,
    canonicalize_daily_market,
    canonicalize_futures,
    canonicalize_rates,
)
from data_pipeline.contracts import MIGRATION_RULES, SCHEMAS, validate_csv, validate_csv_bytes
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
_DECLARED_OUTPUTS = (
    (re.compile(r"data/source/rates/rates_\d{4}\.csv\Z"), "historical_rates"),
    (re.compile(r"data/source/futures/futures_settlements_\d{4}\.csv\Z"), "historical_futures_settlements"),
    (re.compile(r"data/canonical/reference/contract_risk_\d{4}\.csv\Z"), "contract_risk"),
    (re.compile(r"data/canonical/market/daily_market_\d{4}\.csv\Z"), "daily_market"),
    (re.compile(r"data/manifests/p24_inputs\.csv\Z"), "run_inputs"),
)


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


@dataclass(frozen=True)
class _FileState:
    device: int
    inode: int
    size: int
    modified_ns: int
    sha256: str


@dataclass(frozen=True)
class _PublicationEvidence:
    result: MigrationResult
    report_state: _FileState
    output_states: dict[str, _FileState]
    source_states: dict[str, _FileState]


_PUBLISHABLE_RESULTS: dict[int, _PublicationEvidence] = {}


@dataclass
class _DirectoryGuard:
    path: Path
    identity: tuple[int, int]
    token: int
    windows: bool

    def verify(self) -> None:
        current = os.stat(self.path, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != self.identity or self.path.is_symlink() or (
            hasattr(self.path, "is_junction") and self.path.is_junction()
        ):
            raise MigrationError(f"publication directory changed during transaction: {self.path}")

    def close(self) -> None:
        if self.windows:
            import ctypes
            from ctypes import wintypes
            close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            close_handle(self.token)
        else:
            os.close(self.token)


def _acquire_directory_guard(path: Path) -> _DirectoryGuard:
    checked = path.resolve(strict=True)
    if checked.is_symlink() or (hasattr(checked, "is_junction") and checked.is_junction()):
        raise MigrationError(f"publication directory must not be a link: {path}")
    state = os.stat(checked, follow_symlinks=False)
    identity = (state.st_dev, state.st_ino)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        handle = create_file(str(checked), 0x80000000, 0x1 | 0x2, None, 3, 0x02000000, None)
        invalid = ctypes.c_void_p(-1).value
        if handle == invalid:
            raise MigrationError(f"cannot lock publication directory: {path}")
        guard = _DirectoryGuard(checked, identity, int(handle), True)
    else:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        guard = _DirectoryGuard(checked, identity, os.open(checked, flags), False)
    guard.verify()
    return guard


def _guard_for(path: Path, guards: dict[Path, _DirectoryGuard], *, verify: bool = True) -> _DirectoryGuard:
    parent = path.parent.absolute()
    guard = guards.get(parent)
    if guard is None:
        guard = guards.get(parent.resolve(strict=True))
    if guard is None:
        raise MigrationError(f"publication directory is not guarded: {parent}")
    if verify:
        guard.verify()
    return guard


def _replace_sibling(
    source: Path,
    destination: Path,
    guards: dict[Path, _DirectoryGuard],
    *,
    verify: bool = True,
    journal: Callable[[], None] | None = None,
) -> None:
    guard = _guard_for(destination, guards, verify=verify)
    if _guard_for(source, guards, verify=verify) is not guard:
        raise MigrationError("atomic replacement requires same-directory siblings")
    if guard.windows:
        os.replace(source, destination)
    else:
        os.replace(source.name, destination.name, src_dir_fd=guard.token, dst_dir_fd=guard.token)
    if journal is not None:
        journal()
    if verify:
        guard.verify()


def _link_sibling(
    source: Path,
    destination: Path,
    guards: dict[Path, _DirectoryGuard],
    *,
    journal: Callable[[], None] | None = None,
) -> None:
    guard = _guard_for(destination, guards)
    if _guard_for(source, guards) is not guard:
        raise MigrationError("atomic publication requires same-directory siblings")
    if guard.windows:
        os.link(source, destination)
    else:
        os.link(source.name, destination.name, src_dir_fd=guard.token, dst_dir_fd=guard.token, follow_symlinks=False)
    if journal is not None:
        journal()
    guard.verify()


def _unlink_sibling(path: Path, guards: dict[Path, _DirectoryGuard], *, verify: bool = True) -> None:
    guard = _guard_for(path, guards, verify=verify)
    if guard.windows:
        path.unlink(missing_ok=True)
    else:
        try:
            os.unlink(path.name, dir_fd=guard.token)
        except FileNotFoundError:
            pass
    if verify:
        guard.verify()


def _sibling_exists(path: Path, guards: dict[Path, _DirectoryGuard], *, verify: bool = True) -> bool:
    guard = _guard_for(path, guards, verify=verify)
    if guard.windows:
        return path.exists()
    try:
        os.stat(path.name, dir_fd=guard.token, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _capture_sibling(path: Path, label: str, guards: dict[Path, _DirectoryGuard]) -> tuple[bytes, _FileState]:
    guard = _guard_for(path, guards)
    if guard.windows:
        return _capture_file(guard.path, guard.path / path.name, label)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor_number = os.open(path.name, flags, dir_fd=guard.token)
    except OSError as error:
        raise MigrationError(f"{label} is unavailable: {path}") from error
    try:
        before = os.fstat(descriptor_number)
        if not stat.S_ISREG(before.st_mode):
            raise MigrationError(f"{label} is not a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor_number, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor_number)
    finally:
        os.close(descriptor_number)
    named = os.stat(path.name, dir_fd=guard.token, follow_symlinks=False)
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    named_identity = (named.st_dev, named.st_ino, named.st_size, named.st_mtime_ns)
    if before_identity != after_identity or after_identity != named_identity:
        raise MigrationError(f"{label} changed during capture: {path}")
    guard.verify()
    data = b"".join(chunks)
    return data, _FileState(*after_identity, hashlib.sha256(data).hexdigest())


def require_contained(root: Path, candidate: Path) -> Path:
    """Resolve a new or existing child, rejecting every symlink component."""
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise MigrationError(f"repository root is unavailable: {root}") from error
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()) or not resolved_root.is_dir():
        raise MigrationError(f"repository root must be a non-symlink directory: {root}")
    absolute = candidate.absolute()
    relative: Path | None = None
    current: Path | None = None
    for base in (root.absolute(), resolved_root):
        try:
            relative = absolute.relative_to(base)
            current = base
            break
        except ValueError:
            continue
    if relative is None or current is None:
        raise MigrationError(f"path escapes repository: {candidate}")
    for part in relative.parts:
        current /= part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise MigrationError(f"symlink path is not allowed: {current}")
    resolved = candidate.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise MigrationError(f"path escapes repository: {candidate}")
    return resolved


def _repository(root: Path) -> Path:
    root = root.absolute()
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()) or not root.is_dir() or not (root / ".git").exists() or (root / ".git").is_symlink():
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


def _capture_file(root: Path, path: Path, label: str) -> tuple[bytes, _FileState]:
    try:
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except OSError as error:
        raise MigrationError(f"{label} is unavailable: {path}") from error
    relative: Path | None = None
    containment_base: Path | None = None
    for base in (root.absolute(), resolved_root):
        try:
            relative = path.absolute().relative_to(base)
            containment_base = base
            break
        except ValueError:
            continue
    if relative is None or containment_base is None or resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise MigrationError(f"path escapes repository: {path}")
    current = containment_base
    for part in relative.parts:
        current /= part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise MigrationError(f"symlink path is not allowed: {current}")
    checked = resolved_path
    if not checked.is_file() or checked.is_symlink():
        raise MigrationError(f"{label} is not a regular non-symlink file: {path}")
    before = os.stat(checked, follow_symlinks=False)
    with checked.open("rb") as handle:
        descriptor = os.fstat(handle.fileno())
        if (descriptor.st_dev, descriptor.st_ino) != (before.st_dev, before.st_ino):
            raise MigrationError(f"{label} changed before capture: {path}")
        data = handle.read()
    after = os.stat(checked, follow_symlinks=False)
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if after_identity != before_identity:
        raise MigrationError(f"{label} changed during capture: {path}")
    if path.resolve(strict=True) != checked:
        raise MigrationError(f"{label} path changed during capture: {path}")
    return data, _FileState(*after_identity, hashlib.sha256(data).hexdigest())


def _schema_for_output(relative: str) -> str:
    matches = [schema_id for pattern, schema_id in _DECLARED_OUTPUTS if pattern.fullmatch(relative)]
    if len(matches) != 1:
        raise MigrationError(f"undeclared publication path: {relative}")
    return matches[0]


def _validate_report_bytes(data: bytes) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    except UnicodeDecodeError as error:
        raise MigrationError("migration report must be UTF-8") from error
    if reader.fieldnames != list(REPORT_COLUMNS):
        raise MigrationError("migration report header changed")
    rows = list(reader)
    expected_rules = sorted(_SUPPORTED_RULES)
    if [row["rule_id"] for row in rows] != expected_rules:
        raise MigrationError("migration report rule coverage changed")
    if any(row["status"] != "pass" or row["validation_status"] != "pass" for row in rows):
        raise MigrationError("publication requires every migration report row to pass")
    return rows


def _render_report(rows: list[dict[str, str]]) -> bytes:
    rendered = io.StringIO(newline="")
    writer = csv.DictWriter(rendered, fieldnames=REPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return rendered.getvalue().encode("utf-8")


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
                if source_row[column] == "":
                    continue
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
    destination.write_bytes(_render_report(rows))
    return destination


def _register_publishable(
    result: MigrationResult,
    sources: dict[str, Path],
) -> MigrationResult:
    if not result.all_passed:
        return result
    report_data, report_state = _capture_file(result.repo_root, result.report_path, "migration report")
    _validate_report_bytes(report_data)
    output_states: dict[str, _FileState] = {}
    for relative, expected_hash in sorted(result.output_hashes.items()):
        schema_id = _schema_for_output(relative)
        data, state = _capture_file(result.staging_root, result.staging_root / relative, "staged output")
        validate_csv_bytes(SCHEMAS[schema_id], data)
        if state.sha256 != expected_hash:
            raise MigrationError(f"staged output hash changed: {relative}")
        output_states[relative] = state
    source_states = {
        rule_id: _capture_file(result.repo_root, source, "migration source")[1]
        for rule_id, source in sorted(sources.items())
    }
    _PUBLISHABLE_RESULTS[id(result)] = _PublicationEvidence(result, report_state, output_states, source_states)
    return result


def _declared_staged_files(staging: Path) -> dict[str, dict[str, Path]]:
    grouped = {
        "historical_rates": {},
        "historical_futures_settlements": {},
        "contract_risk": {},
        "daily_market": {},
        "run_inputs": {},
    }
    for path in sorted(staging.rglob("*.csv")):
        relative = path.relative_to(staging).as_posix()
        if relative.startswith(".input-snapshots/"):
            continue
        schema_id = _schema_for_output(relative)
        require_contained(staging, path)
        grouped[schema_id][relative] = path
    if set(grouped["run_inputs"]) != {"data/manifests/p24_inputs.csv"}:
        raise MigrationError("staged input manifest is missing or duplicated")
    for schema_id in ("historical_rates", "historical_futures_settlements", "contract_risk", "daily_market"):
        if not grouped[schema_id]:
            raise MigrationError(f"staged {schema_id} partitions are missing")
    return grouped


def _load_existing_migration(repo_root: Path, staging_root: Path, report_path: Path) -> MigrationResult:
    """Reconstruct a fresh result from retained dry-run evidence after a CLI restart."""
    repo = _repository(repo_root)
    staging = require_contained(repo, staging_root)
    report = _report_destination(repo, report_path, staging)
    if not staging.is_dir() or staging.is_symlink() or not (staging / ".git").is_dir():
        raise MigrationError("existing staging evidence is incomplete")
    sources = _discover(repo)
    source_bytes: dict[str, bytes] = {}
    source_hashes: dict[str, str] = {}
    input_rows: list[dict[str, str]] = []
    for rule_id, source in sorted(sources.items()):
        live, live_state = _capture_file(repo, source, "migration source")
        snapshot, _ = _capture_file(staging, staging / ".input-snapshots" / f"{rule_id}.csv", "source snapshot")
        if live != snapshot:
            raise MigrationError(f"source changed since staging: {rule_id}")
        rows = list(csv.DictReader(io.StringIO(live.decode("utf-8-sig"), newline="")))
        dates = [row["date"] for row in rows]
        relative = source.absolute().relative_to(repo.absolute()).as_posix()
        source_bytes[rule_id] = live
        source_hashes[rule_id] = live_state.sha256
        input_rows.append({
            "run_id": "p24-stage",
            "path": relative,
            "sha256": live_state.sha256,
            "row_count": str(len(rows)),
            "start_time": min(dates),
            "end_time": max(dates),
            "schema_version": SCHEMAS["run_inputs"].version,
        })

    grouped = _declared_staged_files(staging)
    manifest_path = grouped["run_inputs"]["data/manifests/p24_inputs.csv"]
    manifest_data, _ = _capture_file(staging, manifest_path, "staged input manifest")
    validate_csv_bytes(SCHEMAS["run_inputs"], manifest_data)
    actual_input_rows = list(csv.DictReader(io.StringIO(manifest_data.decode("utf-8-sig"), newline="")))
    if actual_input_rows != sorted(input_rows, key=lambda row: row["path"]):
        raise MigrationError("staged input manifest no longer matches source snapshots")

    expected_lineage = {
        rule_id: _expected_lineage(rule_id, source_bytes[rule_id])
        for rule_id in sorted(_SUPPORTED_RULES)
    }
    actual_lineage = _actual_lineage(
        grouped["historical_rates"],
        grouped["historical_futures_settlements"],
        grouped["contract_risk"],
        grouped["daily_market"],
    )
    rows = [
        _report_row("cme_swap_master", sources["cme_swap_master"], source_hashes["cme_swap_master"], {**grouped["historical_futures_settlements"], **grouped["contract_risk"]}, expected_lineage["cme_swap_master"], actual_lineage["cme_swap_master"]),
        _report_row("swap_rates", sources["swap_rates"], source_hashes["swap_rates"], grouped["daily_market"], expected_lineage["swap_rates"], actual_lineage["swap_rates"]),
        _report_row("treasury_futures", sources["treasury_futures"], source_hashes["treasury_futures"], grouped["daily_market"], expected_lineage["treasury_futures"], actual_lineage["treasury_futures"]),
        _report_row("treasury_futures_master", sources["treasury_futures_master"], source_hashes["treasury_futures_master"], {**grouped["historical_futures_settlements"], **grouped["contract_risk"]}, expected_lineage["treasury_futures_master"], actual_lineage["treasury_futures_master"]),
        _report_row("treasury_rates", sources["treasury_rates"], source_hashes["treasury_rates"], grouped["historical_rates"], expected_lineage["treasury_rates"], actual_lineage["treasury_rates"]),
    ]
    report_data, _ = _capture_file(repo, report, "migration report")
    _validate_report_bytes(report_data)
    if report_data != _render_report(rows):
        raise MigrationError("migration report no longer matches staged evidence")
    output_paths = {
        relative: path
        for files in grouped.values()
        for relative, path in files.items()
    }
    hashes = {relative: sha256_file(path) for relative, path in sorted(output_paths.items())}
    result = MigrationResult(repo, staging, report, hashes, all(row["status"] == "pass" for row in rows))
    return _register_publishable(result, sources)


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
            return _register_publishable(result, sources)
        return result
    except Exception:
        if created_staging:
            shutil.rmtree(staging)
        raise


def _write_sibling(destination: Path, data: bytes, marker: str, guards: dict[Path, _DirectoryGuard]) -> Path:
    guard = _guard_for(destination, guards)
    if not guard.windows:
        for _ in range(100):
            sibling = f".{destination.name}.{marker}-{secrets.token_hex(8)}.tmp"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(sibling, flags, 0o600, dir_fd=guard.token)
            except FileExistsError:
                continue
            try:
                with os.fdopen(descriptor, "wb", closefd=True) as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                guard.verify()
                return guard.path / sibling
            except Exception:
                try:
                    os.unlink(sibling, dir_fd=guard.token)
                except FileNotFoundError:
                    pass
                raise
        raise MigrationError(f"could not allocate unique publication sibling: {destination}")
    name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=guard.path,
            prefix=f".{destination.name}.{marker}-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        guard.verify()
        return Path(name)
    except Exception:
        if name is not None:
            Path(name).unlink(missing_ok=True)
        raise


def _verify_publication_evidence(result: MigrationResult, evidence: _PublicationEvidence) -> dict[str, bytes]:
    if evidence.result is not result:
        raise MigrationError("publication requires a fresh MigrationResult")
    report_data, report_state = _capture_file(result.repo_root, result.report_path, "migration report")
    if report_state != evidence.report_state:
        raise MigrationError("migration report changed after staging")
    _validate_report_bytes(report_data)
    sources = _discover(result.repo_root)
    if set(sources) != set(evidence.source_states):
        raise MigrationError("migration source set changed after staging")
    for rule_id, source in sorted(sources.items()):
        _, state = _capture_file(result.repo_root, source, "migration source")
        if state != evidence.source_states[rule_id]:
            raise MigrationError(f"migration source changed after staging: {rule_id}")
    captured: dict[str, bytes] = {}
    if result.output_hashes != {relative: state.sha256 for relative, state in evidence.output_states.items()}:
        raise MigrationError("publication requires a fresh MigrationResult")
    for relative, expected_state in sorted(evidence.output_states.items()):
        schema_id = _schema_for_output(relative)
        data, state = _capture_file(result.staging_root, result.staging_root / relative, "staged output")
        if state != expected_state:
            raise MigrationError(f"staged output changed after validation: {relative}")
        validate_csv_bytes(SCHEMAS[schema_id], data)
        captured[relative] = data
    return captured


def publish_migration(result: MigrationResult, repo_root: Path) -> list[Path]:
    """Atomically publish one fully revalidated, one-use migration result."""
    if not isinstance(result, MigrationResult) or not result.all_passed:
        raise MigrationError("publication requires a fully passing migration")
    repo = _repository(repo_root)
    if result.repo_root.absolute() != repo.absolute():
        raise MigrationError("publication repository differs from staged result")
    evidence = _PUBLISHABLE_RESULTS.pop(id(result), None)
    if evidence is None or evidence.result is not result:
        raise MigrationError("publication requires a fresh MigrationResult")
    staged_bytes = _verify_publication_evidence(result, evidence)

    prepared: dict[str, Path] = {}
    backups: dict[str, Path | None] = {}
    claims: dict[str, Path | None] = {}
    destination_states: dict[str, _FileState | None] = {}
    installed: list[str] = []
    claimed: list[str] = []
    guards: dict[Path, _DirectoryGuard] = {}
    destinations: dict[str, Path] = {}
    try:
        for relative in sorted(staged_bytes):
            destination = repo / relative
            require_contained(repo, destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            parent = require_contained(repo, destination.parent)
            guard = guards.get(parent)
            if guard is None:
                guard = _acquire_directory_guard(parent)
            guards[parent] = guard
            guards[destination.parent.absolute()] = guard
            destinations[relative] = destination
        for relative, data in sorted(staged_bytes.items()):
            schema_id = _schema_for_output(relative)
            destination = destinations[relative]
            if _sibling_exists(destination, guards) and (
                destination.is_symlink() or (hasattr(destination, "is_junction") and destination.is_junction())
            ):
                raise MigrationError(f"symlink publication destination is not allowed: {relative}")
            old_data: bytes | None = None
            old_state: _FileState | None = None
            if _sibling_exists(destination, guards):
                old_data, old_state = _capture_sibling(destination, "publication destination", guards)
            destination_states[relative] = old_state

            temporary = _write_sibling(destination, data, "p24-publish", guards)
            prepared[relative] = temporary
            temporary_data, temporary_state = _capture_sibling(temporary, "prepared publication sibling", guards)
            validate_csv_bytes(SCHEMAS[schema_id], temporary_data)
            if temporary_state.sha256 != evidence.output_states[relative].sha256:
                raise MigrationError(f"prepared publication hash mismatch: {relative}")

            if old_data is not None and old_state is not None:
                backup = _write_sibling(destination, old_data, "p24-backup", guards)
                backups[relative] = backup
                backup_data, backup_state = _capture_sibling(backup, "publication backup", guards)
                if backup_data != old_data or backup_state.sha256 != old_state.sha256:
                    raise MigrationError(f"publication backup mismatch: {relative}")
                claim = _write_sibling(destination, b"", "p24-claim", guards)
                claims[relative] = claim
                _unlink_sibling(claim, guards)
            else:
                backups[relative] = None
                claims[relative] = None

        # No destination mutation occurs until every replacement and backup is
        # present, schema-valid, hash-matched, and the original evidence has
        # survived the full preparation interval.
        _verify_publication_evidence(result, evidence)
        for relative in sorted(staged_bytes):
            destination = destinations[relative]
            expected_old = destination_states[relative]
            if expected_old is None:
                if _sibling_exists(destination, guards):
                    raise MigrationError(f"publication destination appeared during preparation: {relative}")
            else:
                _, current = _capture_sibling(destination, "publication destination", guards)
                if current != expected_old:
                    raise MigrationError(f"publication destination changed during preparation: {relative}")
                claim = claims[relative]
                if claim is None or claim.exists():
                    raise MigrationError(f"publication claim path is unavailable: {relative}")
                _replace_sibling(destination, claim, guards, journal=lambda relative=relative: claimed.append(relative))
                _, claimed_state = _capture_sibling(claim, "claimed publication destination", guards)
                if claimed_state != expected_old:
                    _replace_sibling(claim, destination, guards)
                    claimed.pop()
                    raise MigrationError(f"publication destination raced before claim: {relative}")
            # A same-directory hard-link is an atomic no-clobber install: if a
            # path appears after the final check, link fails instead of
            # overwriting bytes that were never part of this transaction.
            _link_sibling(prepared[relative], destination, guards, journal=lambda relative=relative: installed.append(relative))
            _, installed_state = _capture_sibling(destination, "published output", guards)
            if installed_state.sha256 != evidence.output_states[relative].sha256:
                raise MigrationError(f"published hash mismatch: {relative}")
        _verify_publication_evidence(result, evidence)
        return [repo / relative for relative in sorted(staged_bytes)]
    except Exception as error:
        rollback_errors: list[str] = []
        touched = list(dict.fromkeys([*claimed, *installed]))
        for relative in reversed(touched):
            destination = destinations[relative]
            claim = claims.get(relative)
            backup = backups.get(relative)
            try:
                if claim is not None and _sibling_exists(claim, guards, verify=False):
                    _replace_sibling(claim, destination, guards, verify=False)
                elif backup is not None and _sibling_exists(backup, guards, verify=False):
                    _replace_sibling(backup, destination, guards, verify=False)
                elif relative in installed:
                    _unlink_sibling(destination, guards, verify=False)
            except Exception as rollback_error:
                rollback_errors.append(f"{relative}: {rollback_error}")
        if rollback_errors:
            raise MigrationError(f"publication failed and rollback was incomplete: {'; '.join(rollback_errors)}") from error
        if isinstance(error, MigrationError):
            raise
        raise MigrationError(f"publication failed and all destinations were rolled back: {error}") from error
    finally:
        transaction_error = sys.exc_info()[1]
        cleanup_errors: list[str] = []
        try:
            for path in [
                *prepared.values(),
                *(path for path in backups.values() if path is not None),
                *(path for path in claims.values() if path is not None),
            ]:
                try:
                    if path.parent.absolute() in guards:
                        _unlink_sibling(path, guards, verify=False)
                    else:
                        path.unlink(missing_ok=True)
                except Exception as cleanup_error:
                    cleanup_errors.append(f"sibling {path}: {cleanup_error}")
        finally:
            for guard in {id(guard): guard for guard in guards.values()}.values():
                try:
                    guard.close()
                except Exception as cleanup_error:
                    cleanup_errors.append(f"guard {guard.path}: {cleanup_error}")
        if cleanup_errors:
            detail = "; ".join(cleanup_errors)
            if transaction_error is not None:
                transaction_error.add_note(f"publication cleanup was incomplete: {detail}")
            else:
                raise MigrationError(f"publication committed but cleanup was incomplete: {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    if args.publish and args.staging_root.exists() and any(args.staging_root.iterdir()):
        result = _load_existing_migration(args.repo_root, args.staging_root, args.report)
    else:
        result = stage_migration(args.repo_root, args.staging_root, args.report)
    if args.publish:
        started = datetime.now(timezone.utc)
        published = publish_migration(result, args.repo_root)
        ended = datetime.now(timezone.utc)
        print(json.dumps({
            "publication_started_at_utc": started.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "publication_ended_at_utc": ended.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "published": [path.absolute().relative_to(result.repo_root.absolute()).as_posix() for path in published],
        }, sort_keys=True, separators=(",", ":")))
    return 0 if result.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
