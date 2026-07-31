from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path


class SourceChangedError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuditPaths:
    repo_root: Path
    data_root: Path
    inventory_output: Path
    lineage_output: Path


def _resolved_directory(path: Path, label: str) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"{label} must be a directory: {resolved}")
    return resolved


def validate_paths(
    repo_root: Path,
    data_root: Path,
    inventory_output: Path,
    lineage_output: Path,
) -> AuditPaths:
    repo = _resolved_directory(repo_root, "repo root")
    data = _resolved_directory(data_root, "data root")
    outputs = (inventory_output.resolve(), lineage_output.resolve())
    if any(repo not in output.parents for output in outputs):
        raise ValueError("output must be inside repo root")
    return AuditPaths(repo, data, *outputs)


def _csv_files(directory: Path) -> list[Path]:
    if directory.is_symlink():
        return []

    artifacts: list[Path] = []
    for path in directory.iterdir():
        if path.is_symlink():
            continue
        if path.is_dir():
            artifacts.extend(_csv_files(path))
        elif path.is_file() and path.suffix.lower() == ".csv":
            artifacts.append(path)
    return artifacts


def discover_artifacts(data_root: Path) -> list[Path]:
    data_root.resolve(strict=True)
    root = data_root
    data_directory = root / "data"
    artifacts = _csv_files(data_directory) if data_directory.is_dir() else []
    r2_manifest = root / "r2_objects.csv"
    if r2_manifest.is_file() and not r2_manifest.is_symlink():
        artifacts.append(r2_manifest)
    return sorted(artifacts, key=lambda path: path.relative_to(root).as_posix())


def read_raw_header(path: Path) -> tuple[str, ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(next(csv.reader(handle)))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_unchanged(before: dict[Path, str]) -> None:
    changed = [path for path, digest in before.items() if sha256_file(path) != digest]
    if changed:
        names = ", ".join(str(path) for path in sorted(changed))
        raise SourceChangedError(f"source changed during audit: {names}")
