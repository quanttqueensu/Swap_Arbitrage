from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from agents.agent_1.active_groups import resolve_active_groups
from agents.agent_1.contract_risk import ContractRisk
from agents.agent_1.models import (
    BoundContract, BrokerSnapshot, MaturityReconciliation, PositionState, QuoteSnapshot,
)
from agents.agent_1.order_groups import build_order_group, group_to_state
from agents.agent_1.state import AgentState


class ActiveGroupResolutionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
        self.bindings = {
            "2Y:swap": BoundContract("2Y", "swap", 1, "YIT", "YITM26", Decimal(".01"), "r1"),
            "2Y:treasury": BoundContract("2Y", "treasury", 2, "ZT", "ZTU26", Decimal(".01"), "r2"),
            "5Y:swap": BoundContract("5Y", "swap", 3, "YIW", "YIWM26", Decimal(".01"), "r3"),
            "5Y:treasury": BoundContract("5Y", "treasury", 4, "ZF", "ZFU26", Decimal(".01"), "r4"),
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
            con_id: ContractRisk(con_id, f"r{con_id}", self.now.date(), Decimal("20"), -1, "test")
            for con_id in (1, 2, 3, 4)
        }
        self.state = AgentState(active_groups={self.group.group_id: group_to_state(self.group)})

    def snapshot(self, positions):
        return BrokerSnapshot(self.now, positions, (), {})

    def test_completed_group_is_removed_and_normal_cycle_may_resume_next_poll(self):
        result = resolve_active_groups(
            self.state, snapshot=self.snapshot({1: 10, 2: -5, 3: 0, 4: 0}),
            bindings=self.bindings, risks=self.risks,
            max_residual_fraction=Decimal("1"), now=self.now,
        )
        self.assertEqual(result.completed_group_ids, (self.group.group_id,))
        self.assertFalse(result.blocks_normal_cycle)
        self.assertIsNone(result.recovery_target)

    def test_partial_fill_builds_recovery_target_that_only_hedges_lagging_leg(self):
        result = resolve_active_groups(
            self.state, snapshot=self.snapshot({1: 6, 2: 0, 3: 0, 4: 0}),
            bindings=self.bindings, risks=self.risks,
            max_residual_fraction=Decimal("1"), now=self.now,
        )
        self.assertTrue(result.blocks_normal_cycle)
        self.assertEqual(result.supersede_group_ids, (self.group.group_id,))
        self.assertEqual(result.recovery_target.target_2y.swap_qty, 6)
        self.assertEqual(result.recovery_target.target_2y.treasury_qty, -3)
        self.assertEqual(result.recovery_target.target_5y.swap_qty, 0)


if __name__ == "__main__":
    unittest.main()
