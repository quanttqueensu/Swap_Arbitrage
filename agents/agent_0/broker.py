from __future__ import annotations

from typing import Any

from . import config


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


def cancel_all_orders(ib: Any) -> tuple[int, int]:
    before = len(ib.reqAllOpenOrders())
    ib.reqGlobalCancel()
    remaining = before

    for _ in range(
        max(int(config.IBKR_TIMEOUT_SECONDS / config.SUBMISSION_WAIT_SECONDS), 1)
    ):
        ib.sleep(config.SUBMISSION_WAIT_SECONDS)
        remaining = len(ib.reqAllOpenOrders())

        if remaining == 0:
            return before, remaining

    raise RuntimeError(
        f"IBKR global cancellation timed out with {remaining} working order(s)."
    )


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

    for _ in range(20):
        ib.sleep(config.SUBMISSION_WAIT_SECONDS)
        order_status = getattr(trade, "orderStatus", None)
        status = str(getattr(order_status, "status", ""))

        if status not in {"", "ApiPending", "PendingSubmit", "PendingCancel"}:
            break

    return trade
