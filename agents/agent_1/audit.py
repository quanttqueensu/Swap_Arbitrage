from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import BoundContract, BrokerSnapshot, DailyTarget
from .planner import CyclePlan


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


class PaperAuditError(RuntimeError):
    """Raised when broker state cannot be normalized or persisted safely."""


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperAuditError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


def _decimal(
    value: object,
    field_name: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise PaperAuditError(f"{field_name} must be numeric")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise PaperAuditError(f"{field_name} must be numeric") from None
    if not result.is_finite():
        raise PaperAuditError(f"{field_name} must be finite")
    if positive and result <= 0:
        raise PaperAuditError(f"{field_name} must be positive")
    if nonnegative and result < 0:
        raise PaperAuditError(f"{field_name} must be nonnegative")
    return result


def _integer(value: object, field_name: str, *, positive: bool = False) -> int:
    number = _decimal(value, field_name)
    integral = number.to_integral_value()
    if number != integral:
        raise PaperAuditError(f"{field_name} must be an integer")
    result = int(integral)
    if positive and result <= 0:
        raise PaperAuditError(f"{field_name} must be positive")
    return result


def _contract_id(contract: object) -> int:
    con_id = getattr(contract, "conId", None)
    if type(con_id) is not int or con_id <= 0:
        raise PaperAuditError("contract must have a positive integer ID")
    return con_id


def _instrument_id(con_id: int) -> str:
    if type(con_id) is not int or con_id <= 0:
        raise PaperAuditError("contract must have a positive integer ID")
    return f"IBKR:{con_id}"


def _tracked_contracts(bindings: Mapping[str, BoundContract]) -> dict[int, object]:
    contracts: dict[int, object] = {}
    for binding in bindings.values():
        if not isinstance(binding, BoundContract):
            raise PaperAuditError("invalid contract binding")
        contract = binding.broker_contract
        if contract is None or _contract_id(contract) != binding.con_id:
            raise PaperAuditError("bound broker contract is unavailable or mismatched")
        prior = contracts.get(binding.con_id)
        if prior is not None and prior is not contract:
            raise PaperAuditError("duplicate bound contract ID")
        contracts[binding.con_id] = contract
    if not contracts:
        raise PaperAuditError("no bound contracts are available")
    return contracts


def _quote_rows(ib: Any, contracts: dict[int, object], observed_at: datetime) -> list[dict[str, object]]:
    try:
        tickers = list(ib.reqTickers(*contracts.values()))
    except Exception:
        raise PaperAuditError("paper quote request failed") from None

    by_con_id: dict[int, object] = {}
    for ticker in tickers:
        contract = getattr(ticker, "contract", None)
        con_id = _contract_id(contract)
        if con_id in contracts:
            if con_id in by_con_id:
                raise PaperAuditError("duplicate quote for bound contract")
            by_con_id[con_id] = ticker

    if set(by_con_id) != set(contracts):
        raise PaperAuditError("missing quote for bound contract")

    rows: list[dict[str, object]] = []
    for con_id in sorted(contracts):
        ticker = by_con_id[con_id]
        bid = _decimal(getattr(ticker, "bid", None), "bid_price", positive=True)
        ask = _decimal(getattr(ticker, "ask", None), "ask_price", positive=True)
        bid_size = _decimal(getattr(ticker, "bidSize", None), "bid_size", positive=True)
        ask_size = _decimal(getattr(ticker, "askSize", None), "ask_size", positive=True)
        if bid > ask:
            raise PaperAuditError("crossed quote")
        rows.append({
            "timestamp_utc": observed_at,
            "instrument_id": _instrument_id(con_id),
            "bid_price": bid,
            "ask_price": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
        })
    return rows


def _quote_rows_from_snapshot(
    snapshot: BrokerSnapshot,
    *,
    tracked_con_ids: set[int],
) -> list[dict[str, object]] | None:
    if set(snapshot.quotes) != tracked_con_ids:
        raise PaperAuditError("missing quote for bound contract")
    rows: list[dict[str, object]] = []
    observed_at = _utc_datetime(snapshot.observed_at, "snapshot observed_at")
    for con_id in sorted(tracked_con_ids):
        quote = snapshot.quotes[con_id]
        bid = _decimal(quote.bid, "bid_price", positive=True)
        ask = _decimal(quote.ask, "ask_price", positive=True)
        if bid > ask:
            raise PaperAuditError("crossed quote")
        if quote.bid_size is None or quote.ask_size is None:
            return None
        bid_size = _decimal(quote.bid_size, "bid_size", positive=True)
        ask_size = _decimal(quote.ask_size, "ask_size", positive=True)
        rows.append({
            "timestamp_utc": observed_at,
            "instrument_id": _instrument_id(con_id),
            "bid_price": bid,
            "ask_price": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
        })
    return rows


def _position_rows(
    ib: Any,
    *,
    account_id: str,
    tracked_con_ids: set[int],
    observed_at: datetime,
) -> list[dict[str, object]]:
    try:
        items = list(ib.portfolio(account_id))
    except Exception:
        raise PaperAuditError("paper portfolio request failed") from None

    rows: list[dict[str, object]] = []
    seen: set[int] = set()
    for item in items:
        item_account = str(getattr(item, "account", "") or "")
        if item_account and item_account != account_id:
            continue
        contract = getattr(item, "contract", None)
        con_id = _contract_id(contract)
        if con_id not in tracked_con_ids:
            continue
        if con_id in seen:
            raise PaperAuditError("duplicate bound contract in portfolio snapshot")
        seen.add(con_id)
        quantity = _integer(getattr(item, "position", None), "position")
        if quantity == 0:
            continue
        average_cost_value = getattr(item, "averageCost", getattr(item, "avgCost", None))
        rows.append({
            "timestamp_utc": observed_at,
            "instrument_id": _instrument_id(con_id),
            "quantity": quantity,
            "average_cost": _decimal(average_cost_value, "average_cost", positive=True),
            "market_price": _decimal(getattr(item, "marketPrice", None), "market_price", positive=True),
            "unrealized_pnl_usd": _decimal(getattr(item, "unrealizedPNL", None), "unrealized_pnl_usd"),
            "realized_pnl_usd": _decimal(getattr(item, "realizedPNL", None), "realized_pnl_usd"),
        })
    return rows


def _position_rows_from_snapshot(
    snapshot: BrokerSnapshot,
    *,
    tracked_con_ids: set[int],
) -> list[dict[str, object]] | None:
    observed_at = _utc_datetime(snapshot.observed_at, "snapshot observed_at")
    rows: list[dict[str, object]] = []
    for con_id in sorted(tracked_con_ids):
        quantity = snapshot.positions.get(con_id, 0)
        if type(quantity) is not int:
            raise PaperAuditError("snapshot position must be an integer")
        if quantity == 0:
            continue
        detail = snapshot.position_details.get(con_id)
        if detail is None:
            return None
        if detail.quantity != quantity:
            raise PaperAuditError("snapshot position detail is inconsistent")
        rows.append({
            "timestamp_utc": observed_at,
            "instrument_id": _instrument_id(con_id),
            "quantity": quantity,
            "average_cost": _decimal(detail.average_cost, "average_cost", positive=True),
            "market_price": _decimal(detail.market_price, "market_price", positive=True),
            "unrealized_pnl_usd": _decimal(detail.unrealized_pnl_usd, "unrealized_pnl_usd"),
            "realized_pnl_usd": _decimal(detail.realized_pnl_usd, "realized_pnl_usd"),
        })
    return rows


def _tracked_order_refs(submitted_order_ids: Mapping[str, int]) -> dict[int, str]:
    reverse: dict[int, str] = {}
    for order_ref, order_id in submitted_order_ids.items():
        if not isinstance(order_ref, str) or not order_ref.startswith("A1:"):
            continue
        if type(order_id) is not int or order_id <= 0:
            raise PaperAuditError("submitted Agent 1 order ID must be positive")
        if order_id in reverse and reverse[order_id] != order_ref:
            raise PaperAuditError("duplicate Agent 1 broker order ID")
        reverse[order_id] = order_ref
    return reverse


def _fill_rows(
    ib: Any,
    *,
    account_id: str,
    tracked_con_ids: set[int],
    submitted_order_ids: Mapping[str, int],
) -> list[dict[str, object]]:
    reverse = _tracked_order_refs(submitted_order_ids)
    if not reverse:
        return []
    try:
        fills = list(ib.fills())
    except Exception:
        raise PaperAuditError("paper fill request failed") from None

    rows: list[dict[str, object]] = []
    seen_fill_ids: set[str] = set()
    for fill in fills:
        execution = getattr(fill, "execution", None)
        if execution is None:
            continue
        order_id_raw = getattr(execution, "orderId", None)
        if type(order_id_raw) is not int or order_id_raw not in reverse:
            continue
        execution_account = str(getattr(execution, "acctNumber", "") or "")
        if execution_account and execution_account != account_id:
            continue
        con_id = _contract_id(getattr(fill, "contract", None))
        if con_id not in tracked_con_ids:
            continue
        commission_report = getattr(fill, "commissionReport", None)
        if commission_report is None:
            raise PaperAuditError("commission report is required for Agent 1 fill")

        fill_id = str(getattr(execution, "execId", "") or "").strip()
        if not fill_id:
            raise PaperAuditError("Agent 1 fill must have an execution ID")
        if fill_id in seen_fill_ids:
            continue
        seen_fill_ids.add(fill_id)

        side_raw = str(getattr(execution, "side", "") or "").upper()
        side = {"BOT": "BUY", "SLD": "SELL"}.get(side_raw, side_raw)
        if side not in {"BUY", "SELL"}:
            raise PaperAuditError("Agent 1 fill side must be BUY or SELL")
        quantity = _integer(getattr(execution, "shares", None), "fill quantity", positive=True)
        signed_quantity = quantity if side == "BUY" else -quantity
        fill_time = _utc_datetime(getattr(execution, "time", None), "fill_time_utc")
        rows.append({
            "fill_id": fill_id,
            "order_ref": reverse[order_id_raw],
            "fill_time_utc": fill_time,
            "instrument_id": _instrument_id(con_id),
            "side": side,
            "quantity": signed_quantity,
            "fill_price": _decimal(getattr(execution, "price", None), "fill_price", positive=True),
            "commission_usd": _decimal(
                getattr(commission_report, "commission", None),
                "commission_usd",
                nonnegative=True,
            ),
        })
    return rows


def record_paper_audit(
    ib: Any,
    store: Any,
    *,
    account_id: str,
    bindings: Mapping[str, BoundContract],
    submitted_order_ids: Mapping[str, int],
    observed_at: datetime,
    snapshot: BrokerSnapshot | None = None,
) -> dict[str, int]:
    """Record fresh canonical paper quotes, positions, and Agent 1 fills.

    All broker-derived data is normalized before the first store write so a bad
    broker object cannot leave a partial audit snapshot for the cycle.
    """
    if not isinstance(account_id, str) or not account_id.startswith("DU"):
        raise PaperAuditError("DU paper account is required")
    observed_at = _utc_datetime(observed_at, "observed_at")
    contracts = _tracked_contracts(bindings)
    tracked_con_ids = set(contracts)

    quote_rows = (
        _quote_rows_from_snapshot(snapshot, tracked_con_ids=tracked_con_ids)
        if snapshot is not None
        else None
    )
    if quote_rows is None:
        quote_rows = _quote_rows(ib, contracts, observed_at)

    position_rows = (
        _position_rows_from_snapshot(snapshot, tracked_con_ids=tracked_con_ids)
        if snapshot is not None
        else None
    )
    if position_rows is None:
        position_rows = _position_rows(
            ib,
            account_id=account_id,
            tracked_con_ids=tracked_con_ids,
            observed_at=observed_at,
        )
    fill_rows = _fill_rows(
        ib,
        account_id=account_id,
        tracked_con_ids=tracked_con_ids,
        submitted_order_ids=submitted_order_ids,
    )

    try:
        quote_count = store.write("paper_quotes", quote_rows)
        position_count = store.write("paper_positions", position_rows) if position_rows else 0
        fill_count = store.write("paper_fills", fill_rows) if fill_rows else 0
    except Exception:
        raise PaperAuditError("paper audit store write failed") from None

    return {
        "quotes": int(quote_count),
        "positions": int(position_count),
        "fills": int(fill_count),
    }
