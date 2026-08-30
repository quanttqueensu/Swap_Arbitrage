from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def margin_reserve_ok(order_state: Any, reserve_fraction: Decimal) -> bool:
    if (
        type(reserve_fraction) is not Decimal
        or not reserve_fraction.is_finite()
        or reserve_fraction <= 0
        or reserve_fraction > 1
    ):
        return False
    try:
        equity = Decimal(str(order_state.equityWithLoanAfter))
        initial_margin = Decimal(str(order_state.initMarginAfter))
    except (AttributeError, InvalidOperation, ValueError):
        return False
    if not equity.is_finite() or not initial_margin.is_finite() or equity <= 0 or initial_margin < 0:
        return False
    return equity - initial_margin >= reserve_fraction * equity


def preview_margin(
    ib: Any,
    *,
    contract: Any,
    order: Any,
    reserve_fraction: Decimal,
) -> bool:
    try:
        state = ib.whatIfOrder(contract, order)
    except Exception:
        return False
    return margin_reserve_ok(state, reserve_fraction)
