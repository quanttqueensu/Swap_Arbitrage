from __future__ import annotations

import unittest
from types import SimpleNamespace

from agents.agent_1.broker_scope import cancel_agent1_orders, is_agent1_trade


def trade(ref: str, account: str, client_id: int, order_id: int):
    order = SimpleNamespace(
        orderRef=ref,
        account=account,
        clientId=client_id,
        orderId=order_id,
    )
    return SimpleNamespace(order=order)


class FakeIB:
    def __init__(self, trades):
        self.trades = list(trades)
        self.cancelled = []

    def reqAllOpenOrders(self):
        return list(self.trades)

    def cancelOrder(self, order):
        self.cancelled.append(order.orderId)


class BrokerScopeTests(unittest.TestCase):
    def test_identity_requires_prefix_account_and_client(self) -> None:
        candidate = trade("A1:2Y:g1:SWAP", "DU123", 31, 1)
        self.assertTrue(is_agent1_trade(candidate, account_id="DU123", client_id=31))
        self.assertFalse(is_agent1_trade(candidate, account_id="DU999", client_id=31))
        self.assertFalse(is_agent1_trade(candidate, account_id="DU123", client_id=32))

    def test_cancellation_never_touches_manual_agent0_or_other_client_orders(self) -> None:
        ib = FakeIB(
            [
                trade("A1:2Y:g1:SWAP", "DU123", 31, 1),
                trade("agent_0-20260831-01", "DU123", 30, 2),
                trade("", "DU123", 0, 3),
                trade("A1:5Y:g2:SWAP", "DU123", 32, 4),
                trade("A1:5Y:g3:SWAP", "DU999", 31, 5),
            ]
        )

        cancelled = cancel_agent1_orders(ib, account_id="DU123", client_id=31)

        self.assertEqual(cancelled, (1,))
        self.assertEqual(ib.cancelled, [1])


if __name__ == "__main__":
    unittest.main()
