from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from .models import BoundContract, BrokerSnapshot, DailyTarget
from .supervisor import CyclePlan


DECISION_COLUMNS = (
    "decision_id",
    "timestamp_utc",
    "target_version",
    "maturity",
    "desired_swap_qty",
    "desired_treasury_qty",
    "observed_swap_qty",
    "observed_treasury_qty",
    "risk_allowed",
    "risk_reason_codes",
    "action_outcome",
)


class DecisionLogError(RuntimeError):
    """Raised when Agent 1's explanatory decision audit cannot be written safely."""


def _utc_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise DecisionLogError("Decision timestamp must include a timezone.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    ).replace(".000000Z", "Z")


def build_decision_rows(
    *,
    target: DailyTarget | None,
    snapshot: BrokerSnapshot,
    bindings: dict[str, BoundContract],
    plan: CyclePlan,
    now: datetime,
) -> list[dict[str, object]]:
    timestamp = _utc_text(now)
    target_version = target.version if target is not None else ""
    risk_allowed = int(bool(getattr(plan.risk_decision, "allowed", False)))
    reasons = "|".join(plan.reason_codes)
    rows: list[dict[str, object]] = []
    maturities = [
        maturity for maturity in ("2Y", "5Y")
        if f"{maturity}:swap" in bindings and f"{maturity}:treasury" in bindings
    ]
    for maturity in maturities:
        desired = target.for_maturity(maturity) if target is not None else None
        swap = bindings[f"{maturity}:swap"]
        treasury = bindings[f"{maturity}:treasury"]
        rows.append({
            "decision_id": f"A1D:{timestamp}:{maturity}:{target_version}",
            "timestamp_utc": timestamp,
            "target_version": target_version,
            "maturity": maturity,
            "desired_swap_qty": desired.swap_qty if desired is not None else 0,
            "desired_treasury_qty": desired.treasury_qty if desired is not None else 0,
            "observed_swap_qty": snapshot.positions.get(swap.con_id, 0),
            "observed_treasury_qty": snapshot.positions.get(treasury.con_id, 0),
            "risk_allowed": risk_allowed,
            "risk_reason_codes": reasons,
            "action_outcome": plan.action,
        })
    return rows


def _serialized(row: Mapping[str, object]) -> dict[str, str]:
    if set(row) != set(DECISION_COLUMNS):
        raise DecisionLogError("Decision fields do not match the Agent 1 schema.")
    output = {name: str(row[name]) for name in DECISION_COLUMNS}
    if output["maturity"] not in {"2Y", "5Y"}:
        raise DecisionLogError("Decision maturity is invalid.")
    if output["risk_allowed"] not in {"0", "1"}:
        raise DecisionLogError("Decision risk_allowed must be 0 or 1.")
    if not output["decision_id"] or not output["timestamp_utc"] or not output["action_outcome"]:
        raise DecisionLogError("Decision identity fields cannot be empty.")
    for name in (
        "desired_swap_qty", "desired_treasury_qty",
        "observed_swap_qty", "observed_treasury_qty",
    ):
        try:
            int(output[name])
        except ValueError as exc:
            raise DecisionLogError(f"Decision {name} must be an integer.") from exc
    return output


def write_decisions(path: Path, rows: Iterable[Mapping[str, object]]) -> int:
    merged: dict[str, dict[str, str]] = {}
    if path.exists():
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if tuple(reader.fieldnames or ()) != DECISION_COLUMNS:
                    raise DecisionLogError("Existing Agent 1 decision header is invalid.")
                for row in reader:
                    normalized = _serialized(row)
                    merged[normalized["decision_id"]] = normalized
        except OSError as exc:
            raise DecisionLogError("Could not read Agent 1 decision log.") from exc

    for source in rows:
        row = _serialized(source)
        key = row["decision_id"]
        existing = merged.get(key)
        if existing is not None and existing != row:
            raise DecisionLogError("Conflicting Agent 1 decision ID.")
        merged[key] = row

    ordered = sorted(
        merged.values(),
        key=lambda row: (row["timestamp_utc"], row["maturity"], row["decision_id"]),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=DECISION_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(ordered)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except (OSError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        raise DecisionLogError("Agent 1 decision log write failed.") from exc
    return len(ordered)
