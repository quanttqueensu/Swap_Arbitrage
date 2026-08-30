from __future__ import annotations

import unittest
from types import SimpleNamespace

from agents.agent_1.broker import cancel_group_orders


class FakeIB:
    def __init__(self, trades):
        self.trades = trades
        self.cancelled = []
    def reqAllOpenOrders(self):
        return self.trades
    def cancelOrder(self, order):
        self.cancelled.append(order.orderId)


def trade(ref, order_id, client_id=31, account="DU123"):
    return SimpleNamespace(order=SimpleNamespace(
        orderRef=ref, orderId=order_id, clientId=client_id, account=account,
    ))


class GroupCancellationTests(unittest.TestCase):
    def test_cancels_only_requested_agent1_group(self):
        group = "A1:2Y:abc:0001"
        ib = FakeIB([
            trade(group + ":SWAP", 1),
            trade(group + ":TREASURY", 2),
            trade("A1:5Y:def:0002:SWAP", 3),
            trade("agent_0-x", 4, client_id=30),
        ])
        cancelled = cancel_group_orders(ib, group_id=group, account_id="DU123", client_id=31)
        self.assertEqual(cancelled, (1, 2))
        self.assertEqual(ib.cancelled, [1, 2])


if __name__ == "__main__":
    unittest.main()
