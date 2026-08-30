from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agents.agent_1.models import (
    BoundContract,
    MaturityReconciliation,
    PositionState,
    QuoteSnapshot,
)
from agents.agent_1.orders import (
    build_order_group,
    group_is_timed_out,
    plan_partial_fill_recovery,
)


class OrderGroupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
        self.bindings = {
            "swap": BoundContract(
                maturity="2Y", leg="swap", con_id=1, symbol="YIT",
                local_symbol="YITM26", min_tick=Decimal("0.005"),
                risk_id="ERIS-YIT-202606",
            ),
            "treasury": BoundContract(
                maturity="2Y", leg="treasury", con_id=2, symbol="ZT",
                local_symbol="ZTU26", min_tick=Decimal("0.01"),
                risk_id="YAHOO-CONTINUOUS-ZT",
            ),
        }
        self.quotes = {
            "swap": QuoteSnapshot(1, Decimal("99"), Decimal("99.01"), self.now),
            "treasury": QuoteSnapshot(2, Decimal("102"), Decimal("102.02"), self.now),
        }

    def test_builds_one_group_with_deterministic_leg_refs_and_deadline(self) -> None:
        group = build_order_group(
            maturity="2Y",
            target_version="2026-08-31:abcdef0123456789",
            sequence=7,
            reconciliation=MaturityReconciliation(10, -5, "expand"),
            swap_state=PositionState(0, 0),
            treasury_state=PositionState(0, 0),
            bindings=self.bindings,
            quotes=self.quotes,
            created_at=self.now,
            timeout_seconds=Decimal("30"),
        )

        self.assertEqual(group.group_id, "A1:2Y:abcdef012345:0007")
        self.assertEqual([order.order_ref for order in group.orders], [
            "A1:2Y:abcdef012345:0007:SWAP",
            "A1:2Y:abcdef012345:0007:TREASURY",
        ])
        self.assertEqual(group.expires_at, self.now + timedelta(seconds=30))
        self.assertEqual(group.requested_swap_delta, 10)
        self.assertEqual(group.requested_treasury_delta, -5)

    def test_hold_reconciliation_creates_no_group(self) -> None:
        group = build_order_group(
            maturity="2Y",
            target_version="2026-08-31:abcdef",
            sequence=1,
            reconciliation=MaturityReconciliation(0, 0, "hold"),
            swap_state=PositionState(0, 0),
            treasury_state=PositionState(0, 0),
            bindings=self.bindings,
            quotes=self.quotes,
            created_at=self.now,
            timeout_seconds=Decimal("30"),
        )
        self.assertIsNone(group)

    def test_group_timeout_is_bounded(self) -> None:
        group = build_order_group(
            maturity="2Y", target_version="2026-08-31:abcdef", sequence=1,
            reconciliation=MaturityReconciliation(1, -1, "expand"),
            swap_state=PositionState(0), treasury_state=PositionState(0),
            bindings=self.bindings, quotes=self.quotes,
            created_at=self.now, timeout_seconds=Decimal("30"),
        )
        self.assertFalse(group_is_timed_out(group, self.now + timedelta(seconds=29)))
        self.assertTrue(group_is_timed_out(group, self.now + timedelta(seconds=30)))

    def test_partial_fill_hedges_only_lagging_leg_when_residual_is_allowed(self) -> None:
        group = build_order_group(
            maturity="2Y", target_version="2026-08-31:abcdef", sequence=1,
            reconciliation=MaturityReconciliation(10, -5, "expand"),
            swap_state=PositionState(0), treasury_state=PositionState(0),
            bindings=self.bindings, quotes=self.quotes,
            created_at=self.now, timeout_seconds=Decimal("30"),
        )
        recovery = plan_partial_fill_recovery(
            group,
            swap_confirmed_qty=6,
            treasury_confirmed_qty=0,
            residual_within_limit=True,
        )
        self.assertEqual(recovery.action, "hedge")
        self.assertEqual(recovery.swap_delta, 0)
        self.assertEqual(recovery.treasury_delta, -3)

    def test_partial_fill_flattens_group_induced_exposure_when_residual_fails(self) -> None:
        group = build_order_group(
            maturity="2Y", target_version="2026-08-31:abcdef", sequence=1,
            reconciliation=MaturityReconciliation(10, -5, "expand"),
            swap_state=PositionState(0), treasury_state=PositionState(0),
            bindings=self.bindings, quotes=self.quotes,
            created_at=self.now, timeout_seconds=Decimal("30"),
        )
        recovery = plan_partial_fill_recovery(
            group,
            swap_confirmed_qty=6,
            treasury_confirmed_qty=0,
            residual_within_limit=False,
        )
        self.assertEqual(recovery.action, "flatten")
        self.assertEqual(recovery.swap_delta, -6)
        self.assertEqual(recovery.treasury_delta, 0)

    def test_balanced_partial_fill_waits_for_next_broker_cycle(self) -> None:
        group = build_order_group(
            maturity="2Y", target_version="2026-08-31:abcdef", sequence=1,
            reconciliation=MaturityReconciliation(10, -5, "expand"),
            swap_state=PositionState(0), treasury_state=PositionState(0),
            bindings=self.bindings, quotes=self.quotes,
            created_at=self.now, timeout_seconds=Decimal("30"),
        )
        recovery = plan_partial_fill_recovery(
            group,
            swap_confirmed_qty=6,
            treasury_confirmed_qty=-3,
            residual_within_limit=True,
        )
        self.assertEqual(recovery.action, "wait")
        self.assertEqual((recovery.swap_delta, recovery.treasury_delta), (0, 0))


if __name__ == "__main__":
    unittest.main()
