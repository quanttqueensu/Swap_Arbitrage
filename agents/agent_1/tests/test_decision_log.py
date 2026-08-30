from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from agents.agent_1.contract_risk import PortfolioDV01
from agents.agent_1.decision_log import build_decision_rows, write_decisions
from agents.agent_1.models import BoundContract, BrokerSnapshot, DailyTarget, MaturityReconciliation, MaturityTarget
from agents.agent_1.supervisor import CyclePlan


class DecisionLogTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "agent1_decisions.csv"
        self.now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
        self.target = DailyTarget(
            date(2026, 8, 31), "2026-08-31:abcdef", 0,
            MaturityTarget(2, -1), MaturityTarget(0, 0),
        )
        self.bindings = {
            "2Y:swap": BoundContract("2Y", "swap", 1, "YIT", "YITM26", Decimal(".01"), "r1"),
            "2Y:treasury": BoundContract("2Y", "treasury", 2, "ZT", "ZTU26", Decimal(".01"), "r2"),
            "5Y:swap": BoundContract("5Y", "swap", 3, "YIW", "YIWM26", Decimal(".01"), "r3"),
            "5Y:treasury": BoundContract("5Y", "treasury", 4, "ZF", "ZFU26", Decimal(".01"), "r4"),
        }
        self.snapshot = BrokerSnapshot(self.now, {1: 1, 2: 0, 3: 0, 4: 0}, (), {})
        self.plan = CyclePlan(
            action="trade", reason_codes=("within_limits",), groups=(),
            projected_dv01=PortfolioDV01(Decimal("80"), Decimal("0"), Decimal("0")),
            reconciliations={
                "2Y": MaturityReconciliation(1, -1, "expand"),
                "5Y": MaturityReconciliation(0, 0, "hold"),
            },
            risk_decision=SimpleNamespace(allowed=True),
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_records_target_observed_risk_and_action_for_each_maturity(self):
        rows = build_decision_rows(
            target=self.target, snapshot=self.snapshot, bindings=self.bindings,
            plan=self.plan, now=self.now,
        )
        self.assertEqual(len(rows), 2)
        two = next(row for row in rows if row["maturity"] == "2Y")
        self.assertEqual(two["target_version"], self.target.version)
        self.assertEqual(two["desired_swap_qty"], 2)
        self.assertEqual(two["observed_swap_qty"], 1)
        self.assertEqual(two["risk_allowed"], 1)
        self.assertEqual(two["action_outcome"], "trade")

        write_decisions(self.path, rows)
        write_decisions(self.path, rows)
        with self.path.open(newline="", encoding="utf-8") as handle:
            saved = list(csv.DictReader(handle))
        self.assertEqual(len(saved), 2)
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
