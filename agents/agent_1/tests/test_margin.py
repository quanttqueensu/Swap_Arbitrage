from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace

from agents.agent_1.broker import margin_reserve_ok, preview_margin


class FakeIB:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def whatIfOrder(self, contract, order):
        self.calls.append((contract, order))
        return self.state


class MarginTests(unittest.TestCase):
    def test_accepts_preview_that_preserves_configured_equity_reserve(self) -> None:
        state = SimpleNamespace(equityWithLoanAfter="100000", initMarginAfter="80000")
        self.assertTrue(margin_reserve_ok(state, Decimal("0.15")))

    def test_rejects_preview_below_reserve_or_with_missing_fields(self) -> None:
        too_tight = SimpleNamespace(equityWithLoanAfter="100000", initMarginAfter="90001")
        self.assertFalse(margin_reserve_ok(too_tight, Decimal("0.10")))
        self.assertFalse(margin_reserve_ok(SimpleNamespace(), Decimal("0.10")))

    def test_preview_calls_broker_what_if_without_transmitting(self) -> None:
        ib = FakeIB(SimpleNamespace(equityWithLoanAfter="100000", initMarginAfter="80000"))
        contract = object()
        order = object()
        result = preview_margin(
            ib,
            contract=contract,
            order=order,
            reserve_fraction=Decimal("0.15"),
        )
        self.assertTrue(result)
        self.assertEqual(ib.calls, [(contract, order)])


if __name__ == "__main__":
    unittest.main()
