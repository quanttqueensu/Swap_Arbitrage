from dataclasses import fields, is_dataclass
from decimal import Decimal, getcontext, setcontext
import subprocess
import sys
import unittest

from strategy.costs import CostEstimate, naive_cost, observed_cost
from strategy.models import NamedValue


D = Decimal


def cost_kwargs(**overrides):
    values = dict(
        swap_bid_ask_usd=D("200"),
        treasury_bid_ask_usd=D("300"),
        commission_exchange_usd=D("100"),
        slippage_usd=D("50"),
        roll_close_usd=D("100"),
        roll_open_usd=D("150"),
        financing_not_in_funding_usd=D("100"),
        cost_base_dv01_usd_per_bp=D("1000"),
    )
    values.update(overrides)
    return values


class CostEstimateTests(unittest.TestCase):
    # Mutation caught: dropping a cost component, misnaming it, or normalizing 1,000 USD over 1,000 USD/bp incorrectly.
    def test_naive_cost_itemizes_the_hand_checked_one_bp_example(self):
        estimate = naive_cost(**cost_kwargs())
        self.assertIsNotNone(estimate)
        self.assertEqual(
            estimate.components,
            (
                NamedValue("swap_bid_ask", D("200"), "usd"),
                NamedValue("treasury_bid_ask", D("300"), "usd"),
                NamedValue("commission_exchange", D("100"), "usd"),
                NamedValue("slippage", D("50"), "usd"),
                NamedValue("roll", D("250"), "usd"),
                NamedValue("financing_not_in_funding", D("100"), "usd"),
            ),
        )
        self.assertEqual(estimate.total_cost_usd, D("1000"))
        self.assertEqual(estimate.total_cost_bps, D("1"))

    # Mutation caught: accepting non-directional observed inputs or using naive constants instead of the supplied costs.
    def test_observed_cost_uses_directional_caller_inputs(self):
        estimate = observed_cost(**cost_kwargs(
            swap_bid_ask_usd=D("4"),
            treasury_bid_ask_usd=D("5"),
            commission_exchange_usd=D("6"),
            slippage_usd=D("7"),
            roll_close_usd=D("3"),
            roll_open_usd=D("4"),
            financing_not_in_funding_usd=D("8"),
            cost_base_dv01_usd_per_bp=D("37"),
        ))
        self.assertIsNotNone(estimate)
        self.assertEqual(estimate.components[4].value, D("7"))
        self.assertEqual(estimate.total_cost_usd, D("37"))
        self.assertEqual(estimate.total_cost_bps, D("1"))

    # Mutation caught: treating missing or unrealistic costs as zero.
    def test_costs_fail_closed_for_missing_or_invalid_realistic_inputs(self):
        invalid_values = (
            None,
            D("-0.01"),
            D("NaN"),
            D("Infinity"),
            1,
            1.0,
        )
        for function in (naive_cost, observed_cost):
            for name in cost_kwargs():
                for value in invalid_values:
                    with self.subTest(function=function.__name__, name=name, value=value):
                        self.assertIsNone(function(**cost_kwargs(**{name: value})))

    # Mutation caught: losing precision or changing the caller's Decimal context during calculation.
    def test_costs_preserve_the_callers_decimal_context(self):
        original = getcontext().copy()
        try:
            context = getcontext()
            context.prec = 2
            context.rounding = "ROUND_DOWN"
            before = context.copy()
            estimate = naive_cost(**cost_kwargs(
                swap_bid_ask_usd=D("1.234"),
                treasury_bid_ask_usd=D("0"),
                commission_exchange_usd=D("0"),
                slippage_usd=D("0"),
                roll_close_usd=D("0"),
                roll_open_usd=D("0"),
                financing_not_in_funding_usd=D("0"),
                cost_base_dv01_usd_per_bp=D("1"),
            ))
            self.assertIsNotNone(estimate)
            self.assertEqual(estimate.total_cost_bps, D("1.234"))
            after = getcontext()
            self.assertEqual(after.prec, before.prec)
            self.assertEqual(after.rounding, before.rounding)
            self.assertEqual(after.traps, before.traps)
            self.assertEqual(after.flags, before.flags)
        finally:
            setcontext(original)

    # Mutation caught: making the result mutable or changing its public data shape.
    def test_cost_estimate_is_a_frozen_slotted_record(self):
        self.assertTrue(is_dataclass(CostEstimate))
        self.assertEqual(
            tuple(field.name for field in fields(CostEstimate)),
            ("components", "total_cost_usd", "total_cost_bps"),
        )
        estimate = naive_cost(**cost_kwargs())
        self.assertIsNotNone(estimate)
        with self.assertRaises((AttributeError, TypeError)):
            estimate.total_cost_usd = D("0")


class CostModuleBoundaryTests(unittest.TestCase):
    # Mutation caught: adding a prohibited runtime dependency to the pure cost module.
    def test_cost_module_does_not_load_forbidden_runtime_dependencies(self):
        program = """
import sys
import strategy.costs
forbidden = (
    'pandas', 'ib_insync', 'requests', 'urllib3', 'socket', 'pathlib',
    'agents.agent_0.broker', 'agents.agent_0.orders',
)
print([name for name in forbidden if name in sys.modules])
"""
        result = subprocess.run(
            [sys.executable, "-S", "-c", program],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[]\n")


if __name__ == "__main__":
    unittest.main()
