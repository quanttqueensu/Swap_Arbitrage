from __future__ import annotations

import unittest
from decimal import Decimal

from agents.agent_1.orders import OrderPlanningError, build_leg_order


class OrderPlanningTests(unittest.TestCase):
    def test_buy_uses_ask_derived_tick_aligned_limit(self) -> None:
        order = build_leg_order(
            order_ref="A1:2Y:g1:SWAP",
            maturity="2Y",
            leg="swap",
            delta=3,
            bid=Decimal("99.01"),
            ask=Decimal("99.013"),
            min_tick=Decimal("0.005"),
        )

        self.assertEqual(order.side, "BUY")
        self.assertEqual(order.quantity, 3)
        self.assertEqual(order.limit_price, Decimal("99.015"))
        self.assertEqual(order.order_type, "LMT")

    def test_sell_uses_bid_derived_tick_aligned_limit(self) -> None:
        order = build_leg_order(
            order_ref="A1:5Y:g1:TREASURY",
            maturity="5Y",
            leg="treasury",
            delta=-4,
            bid=Decimal("106.437"),
            ask=Decimal("106.45"),
            min_tick=Decimal("0.005"),
        )

        self.assertEqual(order.side, "SELL")
        self.assertEqual(order.quantity, 4)
        self.assertEqual(order.limit_price, Decimal("106.435"))

    def test_zero_delta_creates_no_order(self) -> None:
        order = build_leg_order(
            order_ref="unused",
            maturity="2Y",
            leg="swap",
            delta=0,
            bid=Decimal("99"),
            ask=Decimal("100"),
            min_tick=Decimal("0.01"),
        )

        self.assertIsNone(order)

    def test_crossed_quote_is_rejected(self) -> None:
        with self.assertRaisesRegex(OrderPlanningError, "crossed"):
            build_leg_order(
                order_ref="A1:2Y:g1:SWAP",
                maturity="2Y",
                leg="swap",
                delta=1,
                bid=Decimal("101"),
                ask=Decimal("100"),
                min_tick=Decimal("0.01"),
            )


if __name__ == "__main__":
    unittest.main()
