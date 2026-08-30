from __future__ import annotations

import unittest

from agents.agent_1.models import MaturityTarget, PositionState
from agents.agent_1.planner import reconcile_maturity


class ReconciliationTests(unittest.TestCase):
    def test_working_quantity_counts_toward_target(self) -> None:
        result = reconcile_maturity(
            target=MaturityTarget(swap_qty=12, treasury_qty=-6),
            swap=PositionState(confirmed_qty=8, working_qty=2),
            treasury=PositionState(confirmed_qty=-5, working_qty=0),
        )

        self.assertEqual(result.swap_delta, 2)
        self.assertEqual(result.treasury_delta, -1)
        self.assertEqual(result.phase, "expand")

    def test_duplicate_poll_has_zero_delta_when_working_orders_complete_target(self) -> None:
        result = reconcile_maturity(
            target=MaturityTarget(swap_qty=12, treasury_qty=-6),
            swap=PositionState(confirmed_qty=10, working_qty=2),
            treasury=PositionState(confirmed_qty=-5, working_qty=-1),
        )

        self.assertEqual(result.swap_delta, 0)
        self.assertEqual(result.treasury_delta, 0)
        self.assertEqual(result.phase, "hold")

    def test_reversal_reduces_to_zero_before_opening_opposite_exposure(self) -> None:
        result = reconcile_maturity(
            target=MaturityTarget(swap_qty=-8, treasury_qty=4),
            swap=PositionState(confirmed_qty=10, working_qty=0),
            treasury=PositionState(confirmed_qty=-5, working_qty=0),
        )

        self.assertEqual(result.swap_delta, -10)
        self.assertEqual(result.treasury_delta, 5)
        self.assertEqual(result.phase, "reduce")

    def test_reduction_on_one_leg_freezes_expansion_on_other_leg(self) -> None:
        result = reconcile_maturity(
            target=MaturityTarget(swap_qty=10, treasury_qty=-5),
            swap=PositionState(confirmed_qty=12, working_qty=0),
            treasury=PositionState(confirmed_qty=-3, working_qty=0),
        )

        self.assertEqual(result.swap_delta, -2)
        self.assertEqual(result.treasury_delta, 0)
        self.assertEqual(result.phase, "reduce")


if __name__ == "__main__":
    unittest.main()
