from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from agents.agent_1.execution import ExecutionError, execute_cycle_plan
from agents.agent_1.models import BoundContract, BrokerSnapshot, DailyTarget, MaturityTarget, QuoteSnapshot
from agents.agent_1.order_groups import build_order_group
from agents.agent_1.models import MaturityReconciliation, PositionState
from agents.agent_1.state import AgentState, load_state
from agents.agent_1.supervisor import CyclePlan
from agents.agent_1.contract_risk import PortfolioDV01


class FakeIB:
    def __init__(self):
        self.place_calls = []
        self.cancelled = []
        self.open_trades = []
        self.next_order_id = 100

    def placeOrder(self, contract, order):
        self.next_order_id += 1
        order.orderId = self.next_order_id
        order.clientId = 31
        trade = SimpleNamespace(
            contract=contract,
            order=order,
            orderStatus=SimpleNamespace(status="Submitted", remaining=order.totalQuantity),
            log=[],
        )
        self.place_calls.append((contract, order))
        self.open_trades.append(trade)
        return trade

    def sleep(self, _seconds):
        return None

    def reqAllOpenOrders(self):
        return list(self.open_trades)

    def cancelOrder(self, order):
        self.cancelled.append(order.orderId)


class FakeStore:
    def __init__(self):
        self.calls = []

    def write(self, schema_id, rows):
        rows = list(rows)
        self.calls.append((schema_id, rows))
        return len(rows)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tempdir.name) / "state.json"
        self.now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
        self.config = SimpleNamespace(
            account="DU123", client_id=31,
        )
        self.bindings = {
            "2Y:swap": BoundContract(
                "2Y", "swap", 1, "YIT", "YITM26", Decimal("0.01"), "r1",
                broker_contract=SimpleNamespace(conId=1),
            ),
            "2Y:treasury": BoundContract(
                "2Y", "treasury", 2, "ZT", "ZTU26", Decimal("0.01"), "r2",
                broker_contract=SimpleNamespace(conId=2),
            ),
        }
        quotes = {
            "swap": QuoteSnapshot(1, Decimal("99"), Decimal("99.01"), self.now),
            "treasury": QuoteSnapshot(2, Decimal("100"), Decimal("100.01"), self.now),
        }
        group = build_order_group(
            maturity="2Y", target_version="2026-08-31:abcdef", sequence=1,
            reconciliation=MaturityReconciliation(2, -1, "expand"),
            swap_state=PositionState(0), treasury_state=PositionState(0),
            bindings={"swap": self.bindings["2Y:swap"], "treasury": self.bindings["2Y:treasury"]},
            quotes=quotes, created_at=self.now, timeout_seconds=Decimal("30"),
        )
        self.plan = CyclePlan(
            action="trade",
            reason_codes=("within_limits",),
            groups=(group,),
            projected_dv01=PortfolioDV01(Decimal("80"), Decimal("0"), Decimal("0")),
            reconciliations={"2Y": MaturityReconciliation(2, -1, "expand")},
            risk_decision=SimpleNamespace(allowed=True),
        )
        self.target = DailyTarget(
            date(2026, 8, 31), "2026-08-31:abcdef", 0,
            MaturityTarget(2, -1), MaturityTarget(0, 0),
        )
        self.snapshot = BrokerSnapshot(self.now, {1: 0, 2: 0}, (), {
            1: QuoteSnapshot(1, Decimal("99"), Decimal("99.01"), self.now),
            2: QuoteSnapshot(2, Decimal("100"), Decimal("100.01"), self.now),
        })

    def tearDown(self):
        self.tempdir.cleanup()

    def factory(self, action, quantity, price):
        return SimpleNamespace(
            action=action, totalQuantity=quantity, lmtPrice=price,
            orderType="LMT", orderId=0, clientId=31,
        )

    def test_persists_group_before_any_submission_and_ids_after_confirmation(self) -> None:
        ib = FakeIB()
        store = FakeStore()
        result = execute_cycle_plan(
            ib=ib, config=self.config, plan=self.plan,
            target=self.target, snapshot=self.snapshot,
            bindings=self.bindings, state=AgentState(), state_path=self.state_path,
            now=self.now, store=store, order_factory=self.factory,
        )

        self.assertEqual(result.submitted_order_ids, {
            "A1:2Y:abcdef:0001:SWAP": 101,
            "A1:2Y:abcdef:0001:TREASURY": 102,
        })
        saved = load_state(self.state_path)
        self.assertEqual(saved.session_order_groups, 1)
        self.assertEqual(saved.submitted_order_ids, result.submitted_order_ids)
        self.assertIn("A1:2Y:abcdef:0001", saved.active_groups)
        self.assertEqual(len(ib.place_calls), 2)
        self.assertTrue(all(order.orderType == "LMT" for _, order in ib.place_calls))
        self.assertTrue(any(schema == "paper_orders" for schema, _ in store.calls))
        order_rows = [row for schema, rows in store.calls if schema == "paper_orders" for row in rows]
        treasury_row = next(row for row in order_rows if row["side"] == "SELL")
        self.assertEqual(treasury_row["quantity"], -1)

    def test_cancel_then_flatten_cycle_only_cancels_agent1_orders_and_submits_nothing(self) -> None:
        ib = FakeIB()
        own_order = self.factory("BUY", 1, 99.0)
        own_order.account = "DU123"
        own_order.orderRef = "A1:2Y:abc:0001:SWAP"
        own_order.orderId = 77
        own_order.clientId = 31
        other_order = self.factory("BUY", 1, 99.0)
        other_order.account = "DU123"
        other_order.orderRef = "agent_0-foo"
        other_order.orderId = 88
        other_order.clientId = 30
        ib.open_trades = [
            SimpleNamespace(order=own_order),
            SimpleNamespace(order=other_order),
        ]
        plan = replace(self.plan, action="cancel_then_flatten", groups=())
        result = execute_cycle_plan(
            ib=ib, config=self.config, plan=plan,
            target=self.target, snapshot=self.snapshot,
            bindings=self.bindings, state=AgentState(), state_path=self.state_path,
            now=self.now, order_factory=self.factory,
        )
        self.assertEqual(result.cancelled_order_ids, (77,))
        self.assertEqual(ib.place_calls, [])

    def test_state_write_failure_before_submission_fails_closed(self) -> None:
        ib = FakeIB()
        bad_state_path = Path(self.tempdir.name) / "directory"
        bad_state_path.mkdir()
        with self.assertRaises(ExecutionError):
            execute_cycle_plan(
                ib=ib, config=self.config, plan=self.plan,
                target=self.target, snapshot=self.snapshot,
                bindings=self.bindings, state=AgentState(), state_path=bad_state_path,
                now=self.now, order_factory=self.factory,
            )
        self.assertEqual(ib.place_calls, [])

    def test_decision_audit_is_written_before_first_broker_submission(self) -> None:
        decision_path = Path(self.tempdir.name) / "agent1_decisions.csv"

        class AuditCheckingIB(FakeIB):
            def placeOrder(inner_self, contract, order):
                self.assertTrue(decision_path.exists())
                return super(AuditCheckingIB, inner_self).placeOrder(contract, order)

        ib = AuditCheckingIB()
        execute_cycle_plan(
            ib=ib, config=self.config, plan=self.plan,
            target=self.target, snapshot=self.snapshot,
            bindings=self.bindings, state=AgentState(), state_path=self.state_path,
            decision_log_path=decision_path, now=self.now, order_factory=self.factory,
        )
        self.assertTrue(decision_path.exists())

    def test_recovery_submission_atomically_supersedes_old_active_group(self) -> None:
        old_group_id = "A1:2Y:old:0009"
        old_state = AgentState(active_groups={old_group_id: {"legacy": "placeholder"}})
        ib = FakeIB()
        result = execute_cycle_plan(
            ib=ib, config=self.config, plan=self.plan, target=self.target,
            snapshot=self.snapshot, bindings=self.bindings, state=old_state,
            state_path=self.state_path, now=self.now, order_factory=self.factory,
            supersede_group_ids=(old_group_id,),
        )
        self.assertNotIn(old_group_id, result.state.active_groups)
        self.assertIn("A1:2Y:abcdef:0001", result.state.active_groups)


if __name__ == "__main__":
    unittest.main()
