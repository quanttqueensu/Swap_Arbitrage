from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from agents.agent_1.contracts import resolve_strategy_bindings
from agents.agent_1.models import BoundContract


class StrategyBindingTests(unittest.TestCase):
    def test_resolves_exact_four_strategy_legs_and_passes_held_ids(self) -> None:
        calls = []

        def resolver(ib, **kwargs):
            calls.append(kwargs)
            return BoundContract(
                maturity=kwargs["maturity"],
                leg=kwargs["leg"],
                con_id=len(calls),
                symbol=kwargs["symbol"],
                local_symbol=kwargs["symbol"] + "X",
                min_tick=Decimal("0.01"),
                risk_id="r" + str(len(calls)),
            )

        bindings = resolve_strategy_bindings(
            object(),
            as_of=date(2026, 8, 31),
            min_days_to_expiry=14,
            held_contracts={"2Y:swap": 99},
            resolver=resolver,
        )

        self.assertEqual(set(bindings), {
            "2Y:swap", "2Y:treasury", "5Y:swap", "5Y:treasury",
        })
        self.assertEqual(calls[0]["symbol"], "YIT")
        self.assertEqual(calls[0]["held_con_id"], 99)
        self.assertEqual(calls[1]["symbol"], "ZT")
        self.assertIsNone(calls[1]["held_con_id"])


if __name__ == "__main__":
    unittest.main()
