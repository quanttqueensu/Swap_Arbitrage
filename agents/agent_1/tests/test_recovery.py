from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from agents.agent_1.models import BoundContract, BrokerSnapshot, WorkingOrderSnapshot
from agents.agent_1.cycle import reconcile_recovery_state
from agents.agent_1.state import AgentState


def binding(key, con_id):
    maturity, leg = key.split(":")
    return BoundContract(
        maturity=maturity,
        leg=leg,
        con_id=con_id,
        symbol="X",
        local_symbol="XX",
        min_tick=Decimal("0.01"),
        risk_id=f"r{con_id}",
    )


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
        self.bindings = {
            "2Y:swap": binding("2Y:swap", 1),
            "2Y:treasury": binding("2Y:treasury", 2),
        }

    def snapshot(self, working=()):
        return BrokerSnapshot(
            observed_at=self.now,
            positions={1: 0, 2: 0},
            working_orders=tuple(working),
            quotes={},
        )

    def test_empty_first_run_state_reconciles_when_broker_has_no_agent_orders(self) -> None:
        result = reconcile_recovery_state(AgentState(), self.snapshot(), self.bindings)
        self.assertTrue(result.reconciled)
        self.assertEqual(result.reasons, ())

    def test_unknown_agent1_working_order_blocks_new_exposure(self) -> None:
        working = (
            WorkingOrderSnapshot("A1:2Y:abc:0001:SWAP", 10, 1, 2, "Submitted"),
        )
        result = reconcile_recovery_state(AgentState(), self.snapshot(working), self.bindings)
        self.assertFalse(result.reconciled)
        self.assertIn("unknown_working_order", result.reasons)

    def test_binding_change_is_a_one_cycle_reconciliation_mismatch(self) -> None:
        state = AgentState(bound_contracts={"2Y:swap": 99, "2Y:treasury": 2})
        result = reconcile_recovery_state(state, self.snapshot(), self.bindings)
        self.assertFalse(result.reconciled)
        self.assertIn("binding_mismatch", result.reasons)

    def test_known_working_order_reconciles(self) -> None:
        ref = "A1:2Y:abc:0001:SWAP"
        state = AgentState(
            bound_contracts={"2Y:swap": 1, "2Y:treasury": 2},
            submitted_order_refs=(ref,),
        )
        working = (WorkingOrderSnapshot(ref, 10, 1, 2, "Submitted"),)
        result = reconcile_recovery_state(state, self.snapshot(working), self.bindings)
        self.assertTrue(result.reconciled)


if __name__ == "__main__":
    unittest.main()
