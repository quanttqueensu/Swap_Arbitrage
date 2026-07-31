from __future__ import annotations

import ast
import csv
import hashlib
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd


FULL_SCAN_LIMIT_BYTES = 25 * 1024 * 1024
SAMPLE_ROWS_PER_EDGE = 1_000


class SourceChangedError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuditPaths:
    repo_root: Path
    data_root: Path
    inventory_output: Path
    lineage_output: Path


@dataclass(frozen=True)
class KeyRule:
    columns: tuple[str, ...] = ()
    status: Literal["verified", "candidate", "unknown"] = "unknown"


@dataclass(frozen=True)
class ColumnProfile:
    ordinal: int
    name: str
    missing_count: int
    constant: bool


@dataclass(frozen=True)
class ArtifactProfile:
    relative_path: str
    size_bytes: int
    sha256: str
    headers: tuple[str, ...]
    duplicate_headers: tuple[str, ...]
    row_count: int
    columns: tuple[ColumnProfile, ...]
    duplicate_column_pairs: tuple[tuple[int, int], ...]
    key_rule: KeyRule
    duplicate_key_count: int | None
    time_column: str | None
    start_time: str | None
    end_time: str | None
    sort_order: str
    scan_mode: str


@dataclass(frozen=True)
class ProfileFailure:
    relative_path: str
    error_type: str
    message: str


CLASSIFICATIONS = frozenset(
    {"source", "canonical", "feature", "decision", "risk", "accounting", "diagnostic", "unused"}
)
STATUSES = frozenset({"verified", "candidate", "discrepancy"})
PIPELINE_ORDER = (
    "raw_price_data.csv",
    "signal_data.csv",
    "risk_data.csv",
)


@dataclass(frozen=True)
class SourceEvidence:
    token: str
    location: str
    line: str


@dataclass(frozen=True)
class LineageRow:
    artifact: str
    column: str
    ordinal: int
    classification: str
    source_or_derivation: str
    writer: str
    consumers: tuple[str, ...]
    unit: str
    evidence: str
    status: str


WRITERS = {
    "raw_price_data.csv": "raw_price_data.py",
    "signal_data.csv": "signal_data.py",
    "risk_data.csv": "risk_data.py",
    "swap_arb_backtest_": "backtest.py",
}
CONSUMERS = {
    "raw_price_data.csv": ("signal_data.py",),
    "signal_data.csv": ("risk_data.py",),
    "risk_data.csv": ("backtest.py",),
}
PURPOSES = {
    "cme_swap_data.csv": "Eris contract price and DV01 research master",
    "treasury_futures_data.csv": "Treasury futures contract price and DV01 research master",
    "treasury_rates.csv": "Treasury and funding rate source table",
    "swap_rates.csv": "Eris price and return source table",
    "treasury_futures.csv": "Treasury futures price and return source table",
    "raw_price_data.csv": "wide merged research input",
    "signal_data.csv": "wide signal and proxy-decision output",
    "risk_data.csv": "wide risk-sizing output",
    "r2_objects.csv": "Cloudflare R2 object metadata inventory",
}
SOURCES = {
    "cme_swap_data.csv": "CME/Eris public source cache",
    "treasury_futures_data.csv": "CME or public Treasury futures source cache",
    "treasury_rates.csv": "US Treasury and New York Fed",
    "swap_rates.csv": "Eris public market data",
    "treasury_futures.csv": "public continuous futures proxy",
    "raw_price_data.csv": "derived from current source tables",
    "signal_data.csv": "derived by signal_data.py",
    "risk_data.csv": "derived by risk_data.py",
    "r2_objects.csv": "Cloudflare R2 listing metadata",
}
UNITS = {
    "date": "date",
    "risk_allowed": "boolean",
    "best_proxy_maturity": "maturity",
}


def writer_for(name: str) -> str:
    if name.startswith("swap_arb_backtest_"):
        return WRITERS["swap_arb_backtest_"]
    return WRITERS.get(name, "none found")


def consumers_for(name: str) -> tuple[str, ...]:
    return CONSUMERS.get(name, ())


def purpose_for(name: str) -> str:
    if name.startswith("swap_arb_backtest_"):
        return "legacy backtest result"
    return PURPOSES.get(name, "unresolved; inspect at P20")


def source_for(name: str) -> str:
    if name.startswith("swap_arb_backtest_"):
        return "derived by backtest.py"
    return SOURCES.get(name, "unresolved; inspect at P20")


CURRENT_KEY_RULES = {
    "cme_swap_data.csv": KeyRule(("date", "ticker"), "candidate"),
    "treasury_futures_data.csv": KeyRule(("date", "ticker"), "candidate"),
    "raw_price_data.csv": KeyRule(("date",), "candidate"),
    "signal_data.csv": KeyRule(("date",), "candidate"),
    "risk_data.csv": KeyRule(("date",), "candidate"),
    "r2_objects.csv": KeyRule(("bucket", "object_key"), "candidate"),
}


def _is_link(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (is_junction is not None and is_junction())


def _resolved_directory(path: Path, label: str, reject_links: bool = False) -> Path:
    if reject_links and _is_link(path):
        raise ValueError(f"{label} must not be a symlink")
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
    data = _resolved_directory(data_root, "data root", reject_links=True)
    outputs = (inventory_output.resolve(), lineage_output.resolve())
    if any(repo not in output.parents for output in outputs):
        raise ValueError("output must be inside repo root")
    return AuditPaths(repo, data, *outputs)


def _csv_files(directory: Path) -> list[Path]:
    if _is_link(directory):
        return []

    artifacts: list[Path] = []
    for path in directory.iterdir():
        if _is_link(path):
            continue
        if path.is_dir():
            artifacts.extend(_csv_files(path))
        elif path.is_file() and path.suffix.lower() == ".csv":
            artifacts.append(path)
    return artifacts


def discover_artifacts(data_root: Path) -> list[Path]:
    if _is_link(data_root):
        raise ValueError("data root must not be a symlink")
    data_root.resolve(strict=True)
    root = data_root
    data_directory = root / "data"
    artifacts = _csv_files(data_directory) if data_directory.is_dir() else []
    r2_manifest = root / "r2_objects.csv"
    if r2_manifest.is_file() and not _is_link(r2_manifest):
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


def _read_rows(path: Path, sampled: bool) -> tuple[tuple[str, ...], list[list[str]], int]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            headers = tuple(next(reader))
        except StopIteration as error:
            raise ValueError("missing CSV header") from error
        head: list[list[str]] = []
        tail: deque[list[str]] = deque(maxlen=SAMPLE_ROWS_PER_EDGE)
        all_rows: list[list[str]] = []
        row_count = 0
        has_width_mismatch = False
        for row in reader:
            row_count += 1
            has_width_mismatch = has_width_mismatch or len(row) != len(headers)
            if sampled:
                if row_count <= SAMPLE_ROWS_PER_EDGE:
                    head.append(row)
                else:
                    tail.append(row)
            else:
                all_rows.append(row)
    if has_width_mismatch:
        raise ValueError("row width differs from header width")
    return headers, (head + list(tail) if sampled else all_rows), row_count


def profile_csv(
    path: Path,
    data_root: Path,
    key_rule: KeyRule = KeyRule(),
    *,
    full_scan_limit_bytes: int = FULL_SCAN_LIMIT_BYTES,
) -> ArtifactProfile:
    size_bytes = path.stat().st_size
    sampled = size_bytes > full_scan_limit_bytes
    headers, rows, row_count = _read_rows(path, sampled)
    if any(len(row) != len(headers) for row in rows):
        raise ValueError("row width differs from header width")
    frame = pd.DataFrame(rows, columns=range(len(headers))).replace("", pd.NA)
    duplicate_headers = tuple(
        sorted(name for name, count in Counter(headers).items() if count > 1)
    )
    columns = tuple(
        ColumnProfile(
            ordinal=ordinal,
            name=name,
            missing_count=int(frame[ordinal].isna().sum()),
            constant=bool(frame[ordinal].nunique(dropna=False) <= 1),
        )
        for ordinal, name in enumerate(headers)
    )
    duplicate_column_pairs = tuple(
        (left, right)
        for left in range(len(headers))
        for right in range(left + 1, len(headers))
        if frame[left].equals(frame[right])
    )
    time_ordinal = next(
        (
            ordinal
            for ordinal, name in enumerate(headers)
            if name == "date" or name.endswith(("_date", "_time"))
        ),
        None,
    )
    if time_ordinal is None:
        time_column = start_time = end_time = None
        sort_order = "not-applicable"
    else:
        time_column = headers[time_ordinal]
        times = frame[time_ordinal].dropna().astype(str)
        start_time = times.min() if not times.empty else None
        end_time = times.max() if not times.empty else None
        sort_order = (
            "ascending"
            if times.is_monotonic_increasing
            else "descending"
            if times.is_monotonic_decreasing
            else "unsorted"
        )
    key_ordinals = [headers.index(name) for name in key_rule.columns]
    duplicate_key_count = (
        int(frame.duplicated(subset=key_ordinals, keep=False).sum())
        if key_ordinals
        else None
    )
    return ArtifactProfile(
        relative_path=path.relative_to(data_root).as_posix(),
        size_bytes=size_bytes,
        sha256=sha256_file(path),
        headers=headers,
        duplicate_headers=duplicate_headers,
        row_count=row_count,
        columns=columns,
        duplicate_column_pairs=duplicate_column_pairs,
        key_rule=key_rule,
        duplicate_key_count=duplicate_key_count,
        time_column=time_column,
        start_time=start_time,
        end_time=end_time,
        sort_order=sort_order,
        scan_mode="sampled:first-and-last-1000-rows" if sampled else "full",
    )


def profile_artifacts(
    paths: list[Path],
    data_root: Path,
    key_rules: dict[str, KeyRule],
) -> list[ArtifactProfile | ProfileFailure]:
    results: list[ArtifactProfile | ProfileFailure] = []
    for path in sorted(paths, key=lambda item: item.relative_to(data_root).as_posix()):
        relative_path = path.relative_to(data_root).as_posix()
        key_rule = key_rules.get(
            path.name,
            KeyRule(("date",), "candidate") if path.name.endswith(".csv") else KeyRule(),
        )
        try:
            results.append(profile_csv(path, data_root, key_rule))
        except (csv.Error, UnicodeError, OSError, ValueError) as error:
            results.append(ProfileFailure(relative_path, type(error).__name__, str(error)))
    return results


def verify_unchanged(before: dict[Path, str]) -> None:
    changed = [path for path, digest in before.items() if sha256_file(path) != digest]
    if changed:
        names = ", ".join(str(path) for path in sorted(changed))
        raise SourceChangedError(f"source changed during audit: {names}")


def scan_source_evidence(
    repo_root: Path,
    tokens: set[str],
) -> dict[str, tuple[SourceEvidence, ...]]:
    found: dict[str, set[SourceEvidence]] = {
        token: set() for token in tokens | {"__csv_read__", "__csv_write__"}
    }
    excluded = {".venv", "__pycache__", ".superpowers", ".worktrees"}
    for path in sorted(repo_root.rglob("*.py")):
        if any(part in excluded for part in path.relative_to(repo_root).parts):
            continue
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in tokens:
                relative = path.relative_to(repo_root).as_posix()
                found[node.value].add(
                    SourceEvidence(node.value, f"{relative}:{node.lineno}", lines[node.lineno - 1].strip())
                )
            if isinstance(node, ast.Call):
                function_name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                evidence_token = (
                    "__csv_read__"
                    if function_name == "read_csv"
                    else "__csv_write__"
                    if function_name in {"to_csv", "save_derived_csv"}
                    else ""
                )
                if evidence_token:
                    relative = path.relative_to(repo_root).as_posix()
                    found[evidence_token].add(
                        SourceEvidence(
                            evidence_token,
                            f"{relative}:{node.lineno}",
                            lines[node.lineno - 1].strip(),
                        )
                    )
    return {
        token: tuple(sorted(items, key=lambda item: (item.location, item.line)))
        for token, items in found.items()
    }


def _unit_for(column: str) -> tuple[str, bool]:
    if column in UNITS:
        return UNITS[column], True
    if column.endswith("_bps"):
        return "basis_points", True
    if column.endswith("_price"):
        return "price_points", True
    if column.endswith(("_return", "_pct")):
        return "decimal", True
    if "_contracts_" in column or column.startswith("prior_position_"):
        return "contracts", True
    if any(marker in column for marker in ("_notional_", "_pnl", "_cost", "_equity", "drawdown")):
        return "usd", True
    if any(marker in column for marker in ("_z", "_scale_", "_rank_", "_direction_")):
        return "dimensionless", True
    return "unknown", False


def _classification_for(artifact: str, column: str) -> tuple[str, bool]:
    if artifact == "raw_price_data.csv":
        return "source", True
    if artifact == "signal_data.csv":
        if column.startswith(("proxy_signal_", "proxy_position_", "best_proxy_")):
            return "decision", True
        known = column == "date" or any(
            marker in column
            for marker in ("_price", "_return", "funding_spread_", "_residual", "_z")
        )
        return "feature", known
    if artifact == "risk_data.csv":
        return "risk", column == "date" or any(
            marker in column
            for marker in ("risk_", "_vol_", "_scale_", "_direction_", "_contracts_", "_notional_")
        )
    if artifact.startswith("swap_arb_backtest_"):
        accounting = any(
            marker in column
            for marker in ("prior_position_", "_pnl", "turnover", "cost", "equity", "return", "drawdown")
        )
        return ("accounting", True) if accounting else ("diagnostic", column == "date")
    return "diagnostic", False


def build_lineage(
    profiles: list[ArtifactProfile],
    evidence: dict[str, tuple[SourceEvidence, ...]],
) -> tuple[LineageRow, ...]:
    by_name = {Path(profile.relative_path).name: profile for profile in profiles}
    ordered = [by_name[name] for name in PIPELINE_ORDER if name in by_name]
    ordered.extend(
        sorted(
            (profile for name, profile in by_name.items() if name.startswith("swap_arb_backtest_")),
            key=lambda profile: profile.relative_path,
        )
    )
    rows: list[LineageRow] = []
    for profile in ordered:
        artifact = Path(profile.relative_path).name
        previous_name = (
            "risk_data.csv"
            if artifact.startswith("swap_arb_backtest_")
            else PIPELINE_ORDER[PIPELINE_ORDER.index(artifact) - 1]
            if artifact in PIPELINE_ORDER[1:]
            else None
        )
        previous_headers = set(by_name[previous_name].headers) if previous_name in by_name else set()
        next_names = (
            (PIPELINE_ORDER[PIPELINE_ORDER.index(artifact) + 1],)
            if artifact in PIPELINE_ORDER[:-1]
            else tuple(name for name in by_name if name.startswith("swap_arb_backtest_"))
            if artifact == "risk_data.csv"
            else ()
        )
        for column_profile in profile.columns:
            column = column_profile.name
            copied = column in previous_headers
            locations = tuple(item.location for item in evidence.get(column, ()))
            used_downstream = any(
                column in by_name[name].headers for name in next_names if name in by_name
            )
            classification, classification_verified = _classification_for(artifact, column)
            if copied and not used_downstream and not locations and next_names:
                classification, classification_verified = "unused", True
            unit, unit_verified = _unit_for(column)
            status = (
                "verified"
                if classification_verified and unit_verified
                else "candidate"
                if copied or locations
                else "discrepancy"
            )
            notes = list(locations)
            if status == "discrepancy":
                notes.append("no verified classification or unit")
            rows.append(
                LineageRow(
                    artifact=artifact,
                    column=column,
                    ordinal=column_profile.ordinal,
                    classification=classification,
                    source_or_derivation=(
                        f"copied from {previous_name}:{column}"
                        if copied
                        else "source field"
                        if artifact == "raw_price_data.csv"
                        else f"derived in {writer_for(artifact)}"
                    ),
                    writer=writer_for(artifact),
                    consumers=tuple(
                        consumer
                        for consumer in consumers_for(artifact)
                        if used_downstream
                    ),
                    unit=unit,
                    evidence="; ".join(notes) or "pipeline header comparison",
                    status=status,
                )
            )
    return tuple(rows)
