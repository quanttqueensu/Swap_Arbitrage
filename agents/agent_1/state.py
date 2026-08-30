from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
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
    next_group_sequence: int = 0
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
    if type(state.next_group_sequence) is not int or state.next_group_sequence < 0:
        raise StateError("state next_group_sequence is invalid.")
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


def _sequence_from_ref(value: object) -> int:
    if type(value) is not str:
        return 0
    parts = value.split(":")
    if len(parts) < 4 or parts[0] != "A1":
        return 0
    try:
        sequence = int(parts[3])
    except (TypeError, ValueError):
        return 0
    return sequence if sequence > 0 else 0


def _infer_next_group_sequence(raw: dict[str, object], session_order_groups: int) -> int:
    if "next_group_sequence" in raw:
        configured = raw.get("next_group_sequence")
        if type(configured) is int and configured >= 0:
            return configured
    active_groups = raw.get("active_groups", {})
    submitted_refs = raw.get("submitted_order_refs", [])
    candidates = [session_order_groups]
    if isinstance(active_groups, dict):
        candidates.extend(_sequence_from_ref(key) for key in active_groups)
    if isinstance(submitted_refs, list):
        candidates.extend(_sequence_from_ref(value) for value in submitted_refs)
    return max(candidates, default=0)


def _active_order_refs(state: AgentState) -> tuple[str, ...]:
    prefixes = tuple(f"{group_id}:" for group_id in state.active_groups)
    if not prefixes:
        return ()
    return tuple(
        ref for ref in state.submitted_order_refs
        if ref.startswith(prefixes)
    )


def roll_session(state: AgentState, session_date: str) -> AgentState:
    """Reset per-session counters while retaining monotonic group identity.

    Completed order tracking is bounded to the current session. If an active
    group crosses the session boundary, only its order identities are retained
    so recovery can still reconcile broker truth safely.
    """
    try:
        date.fromisoformat(session_date)
    except (TypeError, ValueError) as exc:
        raise StateError("session_date must be an ISO date.") from exc
    if state.session_pnl_date == session_date:
        return state

    active_refs = _active_order_refs(state)
    active_ref_set = set(active_refs)
    active_ids = {
        ref: order_id
        for ref, order_id in state.submitted_order_ids.items()
        if ref in active_ref_set
    }
    return replace(
        state,
        submitted_order_refs=active_refs,
        submitted_order_ids=active_ids,
        session_order_groups=0,
        session_pnl_date=session_date,
        session_peak_pnl_usd=Decimal("0"),
    )


def load_state(path: Path) -> AgentState:
    if not path.exists():
        return AgentState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("not an object")
        session_order_groups = raw.get("session_order_groups", 0)
        state = AgentState(
            target_version=raw.get("target_version", ""),
            bound_contracts=raw.get("bound_contracts", {}),
            submitted_order_refs=tuple(raw.get("submitted_order_refs", [])),
            submitted_order_ids=raw.get("submitted_order_ids", {}),
            active_groups=raw.get("active_groups", {}),
            last_successful_broker_snapshot=raw.get("last_successful_broker_snapshot", {}),
            session_order_groups=session_order_groups,
            next_group_sequence=_infer_next_group_sequence(raw, session_order_groups),
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
