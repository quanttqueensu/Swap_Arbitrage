from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class AccountRiskError(RuntimeError):
    """Raised when IBKR account-level paper risk cannot be normalized."""


@dataclass(frozen=True)
class DrawdownState:
    peak_pnl_usd: Decimal
    drawdown_usd: Decimal


def _finite_decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AccountRiskError(f"Invalid {label}.") from exc
    if not parsed.is_finite():
        raise AccountRiskError(f"Invalid {label}.")
    return parsed


def collect_session_pnl(
    ib: Any,
    account_id: str,
    *,
    wait_seconds: float = 0.25,
) -> Decimal:
    if not str(account_id).upper().startswith("DU"):
        raise AccountRiskError("Session P&L requires a DU paper account.")
    subscribed = False
    try:
        pnl = ib.reqPnL(account_id, "")
        subscribed = True
        ib.sleep(wait_seconds)
        return _finite_decimal(getattr(pnl, "dailyPnL", None), "IBKR daily P&L")
    except AccountRiskError:
        raise
    except Exception as exc:
        raise AccountRiskError("Could not collect IBKR daily P&L.") from exc
    finally:
        if subscribed:
            try:
                ib.cancelPnL(account_id, "")
            except Exception:
                pass


def update_drawdown(previous_peak_pnl_usd: Decimal, session_pnl_usd: Decimal) -> DrawdownState:
    peak = _finite_decimal(previous_peak_pnl_usd, "session peak P&L")
    current = _finite_decimal(session_pnl_usd, "session P&L")
    new_peak = max(peak, current)
    drawdown = new_peak - current
    if drawdown < 0:
        raise AccountRiskError("Calculated drawdown cannot be negative.")
    return DrawdownState(new_peak, drawdown)
