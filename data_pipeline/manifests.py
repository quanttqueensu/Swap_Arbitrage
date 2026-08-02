from __future__ import annotations

import csv
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from data_pipeline.contracts import SCHEMA_VERSION, SCHEMAS, CsvContract, SchemaValidationError, validate_csv


_CHUNK_SIZE = 1024 * 1024
_RUN_ID = "p24"


@dataclass(frozen=True)
class FileManifest:
    path: str
    sha256: str
    row_count: int
    start_time: str
    end_time: str
    schema_version: str


def _normalized_path(value: str) -> str:
    path = value.replace("\\", "/")
    if not path or path.startswith("/") or ":" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError(f"path must be a normalized repository-relative path: {value}")
    return path


def _ordered(rows: Sequence[FileManifest]) -> list[FileManifest]:
    normalized = [
        FileManifest(_normalized_path(row.path), row.sha256, row.row_count, row.start_time, row.end_time, row.schema_version)
        for row in rows
    ]
    if any(row.schema_version != SCHEMA_VERSION for row in normalized):
        raise ValueError(f"manifest schema version must be {SCHEMA_VERSION}")
    if len({row.path for row in normalized}) != len(normalized):
        raise ValueError("manifest paths must be unique")
    return sorted(normalized, key=lambda row: row.path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _temporal_column(contract: CsvContract) -> str:
    for column in contract.columns:
        if column.scalar_type in {"date", "datetime_utc"}:
            return column.name
    raise ValueError(f"contract {contract.schema_id} has no date/time column")


def _repository_relative(path: Path) -> str:
    resolved = path.resolve(strict=True)
    for root in (resolved.parent, *resolved.parents):
        if (root / ".git").exists():
            return resolved.relative_to(root).as_posix()
    return resolved.name


def profile_file(path: Path, contract: CsvContract) -> FileManifest:
    if contract.version != SCHEMA_VERSION:
        raise ValueError(f"contract schema version must be {SCHEMA_VERSION}")
    try:
        row_count = validate_csv(contract, path)
    except SchemaValidationError:
        raise
    if row_count == 0:
        raise ValueError("cannot manifest an empty file")
    temporal_column = _temporal_column(contract)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        times = [row[temporal_column] for row in csv.DictReader(handle)]
    return FileManifest(
        path=_repository_relative(path),
        sha256=sha256_file(path),
        row_count=row_count,
        start_time=min(times),
        end_time=max(times),
        schema_version=contract.version,
    )


def manifest_digest(rows: Sequence[FileManifest]) -> str:
    digest = hashlib.sha256()
    for row in _ordered(rows):
        digest.update(
            ("\x1f".join((row.path, row.sha256, str(row.row_count), row.start_time, row.end_time, row.schema_version)) + "\n").encode("utf-8")
        )
    return digest.hexdigest()


def write_input_manifest(path: Path, rows: Sequence[FileManifest]) -> str:
    ordered = _ordered(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[column.name for column in SCHEMAS["run_inputs"].columns])
            writer.writeheader()
            for row in ordered:
                writer.writerow({"run_id": _RUN_ID, **row.__dict__})
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return manifest_digest(ordered)
