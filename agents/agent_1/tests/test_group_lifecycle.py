from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from agents.agent_1.risk import ContractRisk
from agents.agent_1.lifecycle import evaluate_active_group
from agents.agent_1.models import (
    BoundContract, BrokerSnapshot, MaturityReconciliation, PositionState,
    QuoteSnapshot, WorkingOrderSnapshot,
)
from agents.agent_1.orders import build_order_group


class GroupLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
        self.bindings = {
            "2Y:swap": BoundContract("2Y", "swap", 1, "YIT", "YITM26", Decimal("0.01"), "r1"),
            "2Y:treasury": BoundContract("2Y", "treasury", 2, "ZT", "ZTU26", Decimal("0.01"), "r2"),
        }
        quotes = {
            "swap": QuoteSnapshot(1, Decimal("99"), Decimal("99.01"), self.now),
            "treasury": QuoteSnapshot(2, Decimal("100"), Decimal("100.01"), self.now),
        }
        self.group = build_order_group(
            maturity="2Y", target_version="2026-08-31:abcdef", sequence=1,
            reconciliation=MaturityReconciliation(10, -5, "expand"),
            swap_state=PositionState(0), treasury_state=PositionState(0),
            bindings={"swap": self.bindings["2Y:swap"], "treasury": self.bindings["2Y:treasury"]},
            quotes=quotes, created_at=self.now, timeout_seconds=Decimal("30"),
        )
        self.risks = {
            1: ContractRisk(1, "r1", self.now.date(), Decimal("20"), -1, "test"),
            2: ContractRisk(2, "r2", self.now.date(), Decimal("40"), -1, "test"),
        }

    def snapshot(self, *, positions=None, working=(), observed_at=None):
        return BrokerSnapshot(
            observed_at=observed_at or self.now,
            positions=positions or {1: 0, 2: 0},
            working_orders=tuple(working), quotes={},
        )

    def test_timeout_requests_group_scoped_cancellation(self):
        working = (
            WorkingOrderSnapshot(self.group.orders[0].order_ref, 101, 1, 10, "Submitted"),
        )
        decision = evaluate_active_group(
            self.group,
            snapshot=self.snapshot(working=working, observed_at=self.now + timedelta(seconds=31)),
            bindings=self.bindings, risks=self.risks,
            max_residual_fraction=Decimal("0.10"),
            now=self.now + timedelta(seconds=31),
        )
        self.assertEqual(decision.action, "cancel_timeout")
        self.assertEqual(decision.group_id, self.group.group_id)

    def test_partial_fill_with_working_remainder_cancels_before_recovery_order(self):
        working = (
            WorkingOrderSnapshot(self.group.orders[1].order_ref, 102, 2, -5, "Submitted"),
        )
        decision = evaluate_active_group(
            self.group, snapshot=self.snapshot(positions={1: 6, 2: 0}, working=working),
            bindings=self.bindings, risks=self.risks,
            max_residual_fraction=Decimal("1"), now=self.now,
        )
        self.assertEqual(decision.action, "cancel_partial")
        self.assertEqual((decision.swap_delta, decision.treasury_delta), (0, 0))

    def test_partial_fill_without_working_remainder_plans_lagging_hedge(self):
        decision = evaluate_active_group(
            self.group, snapshot=self.snapshot(positions={1: 6, 2: 0}),
            bindings=self.bindings, risks=self.risks,
            max_residual_fraction=Decimal("1"), now=self.now,
        )
        self.assertEqual(decision.action, "hedge")
        self.assertEqual((decision.swap_delta, decision.treasury_delta), (0, -3))

    def test_completed_group_is_removable_from_recovery_state(self):
        decision = evaluate_active_group(
            self.group, snapshot=self.snapshot(positions={1: 10, 2: -5}),
            bindings=self.bindings, risks=self.risks,
            max_residual_fraction=Decimal("0.10"), now=self.now,
        )
        self.assertEqual(decision.action, "complete")

    def test_partial_reduction_never_reopens_filled_exposure_on_residual_breach(self):
        quotes = {
            "swap": QuoteSnapshot(1, Decimal("99"), Decimal("99.01"), self.now),
            "treasury": QuoteSnapshot(2, Decimal("100"), Decimal("100.01"), self.now),
        }
        reduce_group = build_order_group(
            maturity="2Y", target_version="flatten:2026-08-31", sequence=2,
            reconciliation=MaturityReconciliation(-10, 5, "reduce"),
            swap_state=PositionState(10), treasury_state=PositionState(-5),
            bindings={"swap": self.bindings["2Y:swap"], "treasury": self.bindings["2Y:treasury"]},
            quotes=quotes, created_at=self.now, timeout_seconds=Decimal("30"),
        )
        decision = evaluate_active_group(
            reduce_group, snapshot=self.snapshot(positions={1: 4, 2: -5}),
            bindings=self.bindings, risks=self.risks,
            max_residual_fraction=Decimal("0.01"), now=self.now,
        )
        self.assertEqual(decision.action, "hedge")
        self.assertEqual((decision.swap_delta, decision.treasury_delta), (0, 3))


if __name__ == "__main__":
    unittest.main()
