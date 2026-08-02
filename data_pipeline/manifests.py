from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from data_pipeline.contracts import SCHEMA_VERSION, SCHEMAS, CsvContract, validate_csv, validate_csv_bytes


_CHUNK_SIZE = 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class FileManifest:
    path: str
    sha256: str
    row_count: int
    start_time: str
    end_time: str
    schema_version: str


def _repository_root(path: Path) -> Path:
    candidate = path.absolute()
    for parent in (candidate.parent, *candidate.parents):
        if (parent / ".git").exists():
            return parent
    raise ValueError(f"path has no repository root: {path}")


def _approved_repository_root(repo_root: Path) -> Path:
    root = repo_root.absolute()
    git_marker = root / ".git"
    if not root.is_dir() or root.is_symlink() or not git_marker.exists() or git_marker.is_symlink():
        raise ValueError(f"repo_root must be an actual non-symlink repository root: {repo_root}")
    return root.resolve(strict=True)


def _contained_profile_file(repo_root: Path, path: Path) -> tuple[Path, Path]:
    root = _approved_repository_root(repo_root)
    raw_path = path.absolute()
    try:
        relative = raw_path.relative_to(repo_root.absolute())
    except ValueError as error:
        raise ValueError(f"path escapes repository: {path}") from error
    current = repo_root.absolute()
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"symlink path is not allowed: {current}")
    resolved_path = path.resolve(strict=True)
    if resolved_path == root or root not in resolved_path.parents:
        raise ValueError(f"path escapes repository: {path}")
    return root, resolved_path


def _contained_file(path: Path, *, require_exists: bool = True) -> tuple[Path, Path]:
    root = _repository_root(path)
    relative = path.absolute().relative_to(root)
    current = root
    if current.is_symlink():
        raise ValueError(f"symlink path is not allowed: {root}")
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"symlink path is not allowed: {current}")
    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=require_exists)
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise ValueError(f"path escapes repository: {path}")
    return resolved_root, resolved_path


def _normalized_path(value: str) -> str:
    path = value.replace("\\", "/")
    if not path or path.startswith("/") or ":" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError(f"path must be a normalized repository-relative path: {value}")
    return path


def _ordered(rows: Sequence[FileManifest]) -> list[FileManifest]:
    normalized: list[FileManifest] = []
    for row in rows:
        normalized_row = FileManifest(_normalized_path(row.path), row.sha256, row.row_count, row.start_time, row.end_time, row.schema_version)
        if not _SHA256.fullmatch(normalized_row.sha256) or normalized_row.row_count <= 0:
            raise ValueError("manifest hash and row count must be valid")
        if not normalized_row.start_time or not normalized_row.end_time or normalized_row.start_time > normalized_row.end_time:
            raise ValueError("manifest coverage must be valid")
        normalized.append(normalized_row)
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


def _approved_contract(contract: CsvContract) -> CsvContract:
    if SCHEMAS.get(contract.schema_id) is not contract:
        raise ValueError("contract must be an exact approved SCHEMAS entry")
    if contract.version != SCHEMA_VERSION:
        raise ValueError(f"contract schema version must be {SCHEMA_VERSION}")
    return contract


def _snapshot(path: Path) -> bytes:
    chunks: list[bytes] = []
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            chunks.append(chunk)
    return b"".join(chunks)


def profile_file(repo_root: Path, path: Path, contract: CsvContract) -> FileManifest:
    contract = _approved_contract(contract)
    root, source = _contained_profile_file(repo_root, path)
    snapshot = _snapshot(source)
    if not snapshot:
        raise ValueError("cannot manifest an empty file")
    row_count = validate_csv_bytes(contract, snapshot)
    if row_count == 0:
        raise ValueError("cannot manifest an empty file")
    temporal_column = _temporal_column(contract)
    decoded = snapshot.decode("utf-8-sig")
    times = [row[temporal_column] for row in csv.DictReader(io.StringIO(decoded, newline=""))]
    if len(times) != row_count:
        raise ValueError("snapshot changed during validation")
    return FileManifest(
        path=source.relative_to(root).as_posix(),
        sha256=hashlib.sha256(snapshot).hexdigest(),
        row_count=row_count,
        start_time=min(times),
        end_time=max(times),
        schema_version=contract.version,
    )


def manifest_digest(rows: Sequence[FileManifest]) -> str:
    digest = hashlib.sha256()
    for row in _ordered(rows):
        digest.update(("\x1f".join((row.path, row.sha256, str(row.row_count), row.start_time, row.end_time, row.schema_version)) + "\n").encode("utf-8"))
    return digest.hexdigest()


def write_input_manifest(path: Path, run_id: str, rows: Sequence[FileManifest]) -> str:
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id is required")
    ordered = _ordered(rows)
    _, destination = _contained_file(path, require_exists=False)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as handle:
            temporary_name = handle.name
            writer = csv.DictWriter(handle, fieldnames=[column.name for column in SCHEMAS["run_inputs"].columns])
            writer.writeheader()
            for row in ordered:
                writer.writerow({"run_id": run_id, **row.__dict__})
            handle.flush()
            os.fsync(handle.fileno())
        temporary = Path(temporary_name)
        validate_csv(SCHEMAS["run_inputs"], temporary)
        temporary.replace(destination)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return manifest_digest(ordered)
