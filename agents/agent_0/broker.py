from __future__ import annotations

from typing import Any

from . import config
from .contracts import get_instrument
from .models import AgentPosition


def _load_ib_class() -> Any:
    try:
        from ib_insync import IB
    except ImportError as exc:
        raise ImportError(
            "Missing dependency: ib_insync. Install it with:\n\n"
            "pip install ib_insync\n"
        ) from exc

    return IB


def connect(account_id: str) -> Any:
    config.assert_paper_only_settings(account_id)

    IB = _load_ib_class()
    ib = IB()

    print(
        f"[CONNECT] Agent 0 paper IBKR "
        f"{config.IBKR_HOST}:{config.IBKR_PORT}, "
        f"clientId={config.IBKR_CLIENT_ID}, account={account_id}"
    )

    ib.connect(
        host=config.IBKR_HOST,
        port=config.IBKR_PORT,
        clientId=config.IBKR_CLIENT_ID,
        timeout=config.IBKR_TIMEOUT_SECONDS,
    )

    if not ib.isConnected():
        raise RuntimeError("Agent 0 IBKR connection failed.")

    validate_managed_account(ib, account_id)
    print("[OK] Agent 0 connected to IBKR paper account")

    return ib


def disconnect(ib: Any) -> None:
    if ib is not None and ib.isConnected():
        print("[DISCONNECT] Agent 0 IBKR")
        ib.disconnect()


def validate_managed_account(ib: Any, account_id: str) -> None:
    accounts = list(ib.managedAccounts())

    if account_id not in accounts:
        raise RuntimeError(
            f"Configured Agent 0 account {account_id!r} is not visible to this "
            f"IBKR session. Managed accounts: {accounts}"
        )


def load_allowed_positions(ib: Any, account_id: str) -> list[AgentPosition]:
    positions: list[AgentPosition] = []

    for raw_position in ib.positions():
        if getattr(raw_position, "account", None) != account_id:
            continue

        contract = raw_position.contract
        symbol = getattr(contract, "symbol", "")
        instrument = get_instrument(symbol)

        if instrument is None:
            continue

        quantity = int(round(float(raw_position.position)))

        if quantity == 0:
            continue

        avg_cost = getattr(raw_position, "avgCost", None)
        avg_cost = float(avg_cost) if avg_cost is not None else None

        positions.append(
            AgentPosition(
                instrument=instrument,
                account=account_id,
                quantity=quantity,
                avg_cost=avg_cost,
                contract=contract,
            )
        )

    return positions


def submit_order(
    ib: Any,
    account_id: str,
    contract: Any,
    order: Any,
) -> Any:
    config.assert_paper_only_settings(account_id)

    order_account = getattr(order, "account", "")

    if order_account != account_id:
        raise RuntimeError(
            f"Order account {order_account!r} does not match Agent 0 account "
            f"{account_id!r}."
        )

    trade = ib.placeOrder(contract, order)
    ib.sleep(1)
    return trade
