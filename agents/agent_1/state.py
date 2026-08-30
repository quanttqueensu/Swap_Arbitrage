from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from dataclasses import asdict, dataclass, field
from pathlib import Path


class StateError(RuntimeError):
    """Raised when Agent 1 recovery state cannot be safely read or written."""


@dataclass(frozen=True)
class AgentState:
    target_version: str = ""
    bound_contracts: dict[str, int] = field(default_factory=dict)
    submitted_order_refs: tuple[str, ...] = ()
    submitted_order_ids: dict[str, int] = field(default_factory=dict)
    active_groups: dict[str, dict[str, object]] = field(default_factory=dict)
    last_successful_broker_snapshot: dict[str, object] = field(default_factory=dict)
    session_order_groups: int = 0
    session_pnl_date: str = ""
    session_peak_pnl_usd: Decimal = Decimal("0")


def _validate_state(state: AgentState) -> AgentState:
    if type(state.target_version) is not str:
        raise StateError("state target_version must be text.")
    if type(state.bound_contracts) is not dict or any(
        type(key) is not str or type(value) is not int or value <= 0
        for key, value in state.bound_contracts.items()
    ):
        raise StateError("state bound_contracts are invalid.")
    if type(state.submitted_order_refs) is not tuple or any(
        type(value) is not str or not value for value in state.submitted_order_refs
    ):
        raise StateError("state submitted_order_refs are invalid.")
    if type(state.submitted_order_ids) is not dict or any(
        type(key) is not str or not key or type(value) is not int or value <= 0
        for key, value in state.submitted_order_ids.items()
    ):
        raise StateError("state submitted_order_ids are invalid.")
    if type(state.active_groups) is not dict or any(
        type(key) is not str or not key or type(value) is not dict
        for key, value in state.active_groups.items()
    ):
        raise StateError("state active_groups are invalid.")
    if type(state.last_successful_broker_snapshot) is not dict:
        raise StateError("state broker snapshot is invalid.")
    if type(state.session_order_groups) is not int or state.session_order_groups < 0:
        raise StateError("state session_order_groups is invalid.")
    if type(state.session_pnl_date) is not str:
        raise StateError("state session_pnl_date is invalid.")
    if state.session_pnl_date:
        try:
            date.fromisoformat(state.session_pnl_date)
        except ValueError as exc:
            raise StateError("state session_pnl_date is invalid.") from exc
    if type(state.session_peak_pnl_usd) is not Decimal or not state.session_peak_pnl_usd.is_finite():
        raise StateError("state session_peak_pnl_usd is invalid.")
    return state


def load_state(path: Path) -> AgentState:
    if not path.exists():
        return AgentState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("not an object")
        state = AgentState(
            target_version=raw.get("target_version", ""),
            bound_contracts=raw.get("bound_contracts", {}),
            submitted_order_refs=tuple(raw.get("submitted_order_refs", [])),
            submitted_order_ids=raw.get("submitted_order_ids", {}),
            active_groups=raw.get("active_groups", {}),
            last_successful_broker_snapshot=raw.get("last_successful_broker_snapshot", {}),
            session_order_groups=raw.get("session_order_groups", 0),
            session_pnl_date=raw.get("session_pnl_date", ""),
            session_peak_pnl_usd=Decimal(str(raw.get("session_peak_pnl_usd", "0"))),
        )
        return _validate_state(state)
    except (OSError, ValueError, TypeError, InvalidOperation, json.JSONDecodeError, StateError) as exc:
        raise StateError("Agent 1 recovery state is invalid or unreadable.") from exc


def save_state(path: Path, state: AgentState) -> None:
    _validate_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        payload = asdict(state)
        payload["submitted_order_refs"] = list(state.submitted_order_refs)
        payload["session_peak_pnl_usd"] = str(state.session_peak_pnl_usd)
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except (OSError, TypeError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        raise StateError("Agent 1 recovery state write failed.") from exc
