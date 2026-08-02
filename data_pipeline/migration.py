"""Offline, deterministic staging for the approved canonical migration."""

from __future__ import annotations

import argparse
import csv
import hashlib
from dataclasses import dataclass
from datetime import date, time, timedelta, timezone
from pathlib import Path
from typing import Iterable

from data_pipeline.canonicalize import (
    SourceTiming,
    canonicalize_daily_market,
    canonicalize_futures,
    canonicalize_rates,
)
from data_pipeline.contracts import MIGRATION_RULES, SCHEMAS, validate_csv
from data_pipeline.manifests import profile_file, sha256_file, write_input_manifest


REPORT_COLUMNS = (
    "rule_id", "source_paths", "source_sha256", "source_key_count", "output_paths",
    "output_sha256", "output_key_count", "status", "detail",
)
_SUPPORTED_RULES = frozenset({"cme_swap_master", "treasury_futures_master", "swap_rates", "treasury_futures", "treasury_rates"})
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


def _key_count(rows: Iterable[dict[str, str]]) -> int:
    return len(list(rows))


def _report_row(rule_id: str, source: Path, outputs: dict[str, Path], output_rows: list[dict[str, str]], source_keys: int) -> dict[str, str]:
    hashes = ";".join(f"{path}:{sha256_file(output)}" for path, output in sorted(outputs.items()))
    return {
        "rule_id": rule_id,
        "source_paths": source.as_posix(),
        "source_sha256": sha256_file(source),
        "source_key_count": str(source_keys),
        "output_paths": ";".join(sorted(outputs)),
        "output_sha256": hashes,
        "output_key_count": str(_key_count(output_rows)),
        "status": "pass" if source_keys == _key_count(output_rows) else "fail",
        "detail": "exact rule-specific source/output key reconciliation",
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


def stage_migration(repo_root: Path, staging_root: Path, report_path: Path) -> MigrationResult:
    repo = _repository(repo_root)
    staging = require_contained(repo, staging_root)
    report = require_contained(repo, report_path)
    if staging.exists() and any(staging.iterdir()):
        raise MigrationError(f"staging root must be empty: {staging}")
    if staging.exists() and staging.is_symlink():
        raise MigrationError(f"symlink path is not allowed: {staging}")
    staging.mkdir(parents=True, exist_ok=True)
    (staging / ".git").mkdir(exist_ok=True)  # enables manifest profiling without publication.
    sources = _discover(repo)

    rates = canonicalize_rates(sources["treasury_rates"])
    futures = canonicalize_futures(sources["cme_swap_master"], sources["treasury_futures_master"])
    market = canonicalize_daily_market(sources["swap_rates"], sources["treasury_futures"], _TIMING)
    rate_files = _partitions(staging, "historical_rates", "data/source/rates/rates_YYYY.csv", rates)
    settlement_files = _partitions(staging, "historical_futures_settlements", "data/source/futures/futures_settlements_YYYY.csv", futures.settlements_by_year)
    risk_files = _partitions(staging, "contract_risk", "data/canonical/reference/contract_risk_YYYY.csv", futures.risk_by_year)
    market_files = _partitions(staging, "daily_market", "data/canonical/market/daily_market_YYYY.csv", market)
    profiles = [profile_file(staging, file, SCHEMAS[schema]) for schema, files in (("historical_rates", rate_files), ("historical_futures_settlements", settlement_files), ("contract_risk", risk_files), ("daily_market", market_files)) for file in files.values()]
    manifest = _write_csv(staging, "data/manifests/p24_inputs.csv", "run_inputs", [])
    write_input_manifest(manifest, "p24-stage", profiles)

    settlement_rows = [row for rows in futures.settlements_by_year.values() for row in rows]
    risk_rows = [row for rows in futures.risk_by_year.values() for row in rows]
    market_rows = [row for rows in market.values() for row in rows]
    rate_rows = [row for rows in rates.values() for row in rows]
    rows = [
        _report_row("cme_swap_master", sources["cme_swap_master"], {**settlement_files, **risk_files}, [row for row in settlement_rows if row["source"] == "ERIS"] + [row for row in risk_rows if row["instrument_id"].startswith("ERIS-")], 2),
        _report_row("swap_rates", sources["swap_rates"], market_files, [row for row in market_rows if row["source"] == "ERIS"], 2),
        _report_row("treasury_futures", sources["treasury_futures"], market_files, [row for row in market_rows if row["source"] == "YAHOO"], 2),
        _report_row("treasury_futures_master", sources["treasury_futures_master"], {**settlement_files, **risk_files}, [row for row in settlement_rows if row["source"] == "YAHOO"] + [row for row in risk_rows if row["instrument_id"].startswith("YAHOO-")], 2),
        _report_row("treasury_rates", sources["treasury_rates"], rate_files, rate_rows, 4),
    ]
    report = _write_report(repo, report, rows)
    outputs = [*rate_files, *settlement_files, *risk_files, *market_files, "data/manifests/p24_inputs.csv"]
    hashes = {relative: sha256_file(require_contained(staging, staging / relative)) for relative in sorted(outputs)}
    return MigrationResult(repo, staging, report, hashes, all(row["status"] == "pass" for row in rows))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    result = stage_migration(args.repo_root, args.staging_root, args.report)
    if args.publish:
        raise MigrationError("publication is reserved for Task 5 and is not implemented")
    return 0 if result.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
