from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from agents.agent_1.models import BoundContract, MaturityReconciliation, PositionState, QuoteSnapshot
from agents.agent_1.order_groups import build_order_group, group_from_state, group_to_state


class GroupStateTests(unittest.TestCase):
    def test_group_round_trip_preserves_restart_recovery_fields(self) -> None:
        now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
        bindings = {
            "swap": BoundContract("2Y", "swap", 1, "YIT", "YITM26", Decimal("0.01"), "r1"),
            "treasury": BoundContract("2Y", "treasury", 2, "ZT", "ZTU26", Decimal("0.01"), "r2"),
        }
        quotes = {
            "swap": QuoteSnapshot(1, Decimal("99"), Decimal("99.01"), now),
            "treasury": QuoteSnapshot(2, Decimal("100"), Decimal("100.01"), now),
        }
        group = build_order_group(
            maturity="2Y", target_version="2026-08-31:abcdef", sequence=1,
            reconciliation=MaturityReconciliation(10, -5, "expand"),
            swap_state=PositionState(0), treasury_state=PositionState(0),
            bindings=bindings, quotes=quotes, created_at=now,
            timeout_seconds=Decimal("30"),
        )
        restored = group_from_state(group_to_state(group))
        self.assertEqual(restored, group)


if __name__ == "__main__":
    unittest.main()
