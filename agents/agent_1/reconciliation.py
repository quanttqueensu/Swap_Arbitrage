from __future__ import annotations

from .models import MaturityReconciliation, MaturityTarget, PositionState


def _reduction_delta(effective: int, desired: int) -> int:
    if effective == 0:
        return 0

    if desired == 0 or effective * desired < 0:
        return -effective

    if (effective > 0) == (desired > 0) and abs(desired) < abs(effective):
        return desired - effective

    return 0


def reconcile_maturity(
    *,
    target: MaturityTarget,
    swap: PositionState,
    treasury: PositionState,
) -> MaturityReconciliation:
    swap_effective = swap.effective_qty
    treasury_effective = treasury.effective_qty

    swap_reduction = _reduction_delta(swap_effective, target.swap_qty)
    treasury_reduction = _reduction_delta(treasury_effective, target.treasury_qty)

    if swap_reduction != 0 or treasury_reduction != 0:
        return MaturityReconciliation(
            swap_delta=swap_reduction,
            treasury_delta=treasury_reduction,
            phase="reduce",
        )

    swap_delta = target.swap_qty - swap_effective
    treasury_delta = target.treasury_qty - treasury_effective

    if swap_delta == 0 and treasury_delta == 0:
        phase = "hold"
    else:
        phase = "expand"

    return MaturityReconciliation(
        swap_delta=swap_delta,
        treasury_delta=treasury_delta,
        phase=phase,
    )
