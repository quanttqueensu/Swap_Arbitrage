from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.agent_0 import config
from agents.agent_0.broker import cancel_all_orders, connect, disconnect, submit_order
from agents.agent_0.contracts import get_instrument, resolve_futures
from agents.agent_0.models import QueuedOrder
from agents.agent_0.orders import (
    build_order,
    load_orders,
    roll_tracking,
    save_orders,
    trade_order_id,
    trade_status,
)
from agents.agent_0.random_policy import RandomPolicy
from agents.agent_0.sizing import load_sizing_caps


def margin_reserve_ok(order_state: Any) -> bool:
    try:
        equity = Decimal(str(order_state.equityWithLoanAfter))
        initial_margin = Decimal(str(order_state.initMarginAfter))
    except (AttributeError, InvalidOperation):
        return False

    return equity > 0 and equity - initial_margin >= (
        config.MARGIN_RESERVE_FRACTION * equity
    )


def fit_order_to_margin(
    ib: Any,
    account_id: str,
    contract: Any,
    record: QueuedOrder,
) -> Any | None:
    for quantity in range(record.quantity, 0, -1):
        candidate = replace(record, quantity=quantity)
        order = build_order(account_id, candidate)

        if margin_reserve_ok(ib.whatIfOrder(contract, order)):
            record.quantity = quantity
            return order

    return None


def reconcile_plan(
    existing: list[QueuedOrder],
    proposed: list[QueuedOrder],
) -> list[QueuedOrder]:
    existing_by_ref = {record.order_ref: record for record in existing}
    return [existing_by_ref.get(record.order_ref, record) for record in proposed]


def working_order_counts(trades: list[Any], account_id: str) -> Counter:
    return Counter(
        (
            str(getattr(trade.contract, "conId", "")),
            str(getattr(trade.order, "action", "")),
        )
        for trade in trades
        if getattr(trade.order, "account", "") == account_id
    )


def allocate_contracts(
    records: list[QueuedOrder],
    contracts_by_symbol: dict[str, list[Any]],
    occupied: Counter,
) -> dict[str, Any]:
    counts = Counter(occupied)
    allocated = {}

    for record in records:
        available = [
            contract
            for contract in contracts_by_symbol.get(record.symbol, [])
            if counts[(str(getattr(contract, "conId", "")), record.side)]
            < config.MAX_WORKING_ORDERS_PER_CONTRACT_SIDE
        ]

        if not available:
            raise RuntimeError(
                "Insufficient IBKR working-order capacity for "
                f"{record.symbol} {record.side}; no orders were submitted."
            )

        contract = min(
            available,
            key=lambda item: counts[
                (str(getattr(item, "conId", "")), record.side)
            ],
        )
        contract_id = str(getattr(contract, "conId", ""))
        record.contract_id = contract_id
        allocated[record.order_ref] = contract
        counts[(contract_id, record.side)] += 1

    return allocated


def submit_plan(
    ib: Any,
    account_id: str,
    records: list[QueuedOrder],
    upcoming_path: Path = config.UPCOMING_ORDERS_FILE,
    previous_path: Path = config.PREVIOUS_ORDERS_FILE,
) -> dict[str, int]:
    server_trades = list(ib.reqAllOpenOrders())
    server_orders = {
        str(trade.order.orderRef): trade
        for trade in server_trades
        if getattr(getattr(trade, "order", None), "orderRef", "")
    }
    summary = {
        "planned": len(records),
        "accepted": 0,
        "already_submitted": 0,
        "rejected": 0,
        "margin_blocked": 0,
    }

    for record in records:
        server_trade = server_orders.get(record.order_ref)

        if server_trade is not None:
            record.status = "accepted"
            record.order_id = trade_order_id(server_trade)
            server_contract = getattr(server_trade, "contract", None)
            record.contract_id = str(getattr(server_contract, "conId", ""))
            summary["already_submitted"] += 1
            continue

        record.status = "planned"
        record.order_id = ""
        record.contract_id = ""

    save_orders(upcoming_path, records)
    pending = [record for record in records if record.order_ref not in server_orders]
    contracts_by_symbol = {}

    for symbol in sorted({record.symbol for record in pending}):
        instrument = get_instrument(symbol)

        if instrument is None:
            raise RuntimeError(f"Unknown Agent 0 instrument: {symbol}.")

        contracts_by_symbol[symbol] = resolve_futures(ib, instrument)

    allocated = allocate_contracts(
        pending,
        contracts_by_symbol,
        working_order_counts(server_trades, account_id),
    )
    save_orders(upcoming_path, records)

    for index, record in enumerate(pending):
        contract = allocated[record.order_ref]
        order = fit_order_to_margin(ib, account_id, contract, record)

        if order is None:
            summary["margin_blocked"] = len(pending) - index
            save_orders(upcoming_path, records)
            break

        trade = submit_order(
            ib=ib,
            account_id=account_id,
            contract=contract,
            order=order,
        )
        status = trade_status(trade)
        record.order_id = trade_order_id(trade)
        has_error = any(
            getattr(entry, "errorCode", 0)
            for entry in getattr(trade, "log", [])
        )

        if status in {"ApiCancelled", "Cancelled", "Inactive"} or has_error:
            record.status = "rejected"
            summary["rejected"] += 1
            save_orders(
                upcoming_path,
                [item for item in records if item.order_ref != record.order_ref],
            )
            save_orders(previous_path, [*load_orders(previous_path), record])
            break

        if status in {"", "ApiPending", "PendingSubmit", "PendingCancel"}:
            save_orders(upcoming_path, records)
            raise RuntimeError(
                f"IBKR submission {record.order_ref} was not confirmed; "
                "submission stopped for safe reconciliation."
            )

        record.status = "accepted"
        summary["accepted"] += 1
        save_orders(upcoming_path, records)

    return summary


def queue_next_week(account_id: str | None = None) -> dict[str, int]:
    account_id = config.get_agent_account_id(account_id)
    config.assert_paper_only_settings(account_id)

    now = datetime.now(timezone.utc)
    roll_tracking(now)

    existing = load_orders(config.UPCOMING_ORDERS_FILE)
    today = now.astimezone(ZoneInfo(config.ACTIVATION_TIMEZONE)).date()
    proposed = RandomPolicy().build_week_plan(load_sizing_caps(), today)
    records = reconcile_plan(existing, proposed)

    save_orders(config.UPCOMING_ORDERS_FILE, records)

    ib = None

    try:
        ib = connect(account_id)
        return submit_plan(ib, account_id, records)
    finally:
        disconnect(ib)


def cancel_all_working_orders(
    account_id: str | None = None,
    upcoming_path: Path = config.UPCOMING_ORDERS_FILE,
) -> dict[str, int]:
    account_id = config.get_agent_account_id(account_id)
    config.assert_paper_only_settings(account_id)
    ib = None

    try:
        ib = connect(account_id)
        before, remaining = cancel_all_orders(ib)
        records = load_orders(upcoming_path)

        for record in records:
            record.status = "planned"
            record.contract_id = ""
            record.order_id = ""

        save_orders(upcoming_path, records)
        return {"before": before, "remaining": remaining}
    finally:
        disconnect(ib)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Queue Agent 0 paper orders for the next calendar week."
    )
    parser.add_argument(
        "--account",
        help="IBKR paper account ID. Overrides AGENT0_IBKR_ACCOUNT.",
    )
    parser.add_argument(
        "--cancel-all",
        action="store_true",
        help="Cancel every working order visible to this TWS session and exit.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.cancel_all:
        summary = cancel_all_working_orders(args.account)
        print(
            "[ALL ORDERS CANCELLED] "
            f"before={summary['before']} remaining={summary['remaining']}"
        )
        return

    summary = queue_next_week(args.account)
    print(
        "[WEEK QUEUED] "
        f"planned={summary['planned']} "
        f"accepted={summary['accepted']} "
        f"already_submitted={summary['already_submitted']} "
        f"rejected={summary['rejected']} "
        f"margin_blocked={summary['margin_blocked']}"
    )


if __name__ == "__main__":
    main()
