from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace

from agents.agent_1.orders import build_ib_limit_order
from agents.agent_1.orders import LimitOrderPlan


class IbOrderTests(unittest.TestCase):
    def test_conversion_preserves_limit_only_policy_and_identity(self) -> None:
        plan = LimitOrderPlan(
            order_ref="A1:2Y:abc:0001:SWAP",
            maturity="2Y",
            leg="swap",
            side="BUY",
            quantity=3,
            limit_price=Decimal("99.015"),
        )
        created = []

        def factory(action, quantity, limit_price):
            order = SimpleNamespace(
                action=action,
                totalQuantity=quantity,
                lmtPrice=limit_price,
                orderType="LMT",
            )
            created.append(order)
            return order

        order = build_ib_limit_order("DU123", plan, order_factory=factory)

        self.assertIs(order, created[0])
        self.assertEqual(order.orderType, "LMT")
        self.assertEqual(order.orderRef, plan.order_ref)
        self.assertEqual(order.account, "DU123")
        self.assertEqual(order.tif, "DAY")
        self.assertTrue(order.transmit)
        self.assertEqual(order.lmtPrice, 99.015)

    def test_refuses_non_agent1_order_reference(self) -> None:
        plan = LimitOrderPlan(
            order_ref="other:order",
            maturity="2Y", leg="swap", side="BUY", quantity=1,
            limit_price=Decimal("99"),
        )
        with self.assertRaisesRegex(ValueError, "A1"):
            build_ib_limit_order("DU123", plan, order_factory=lambda *_: SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
