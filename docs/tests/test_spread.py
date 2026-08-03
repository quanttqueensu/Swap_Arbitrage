"""Tests for the frozen P31 spread equations."""

from __future__ import annotations

from decimal import Decimal, getcontext
import hashlib
import json
from pathlib import Path
import unittest

from strategy import TradeDirection
from strategy.spread import (
    STRATEGY_SPEC_VERSION,
    directional_cost_buffer_bps,
    expected_funding_bps,
    fixed_swap_spread_bps,
    funding_spread_bps,
    gross_excess_spread_bps,
    net_opportunity_bps,
    rate_decimal_to_bps,
    tick_value_usd,
    treasury_fractional_quote_to_points,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "strategy_equation_examples.json"
FIXTURE_SHA256 = "3fb9da5fc9ad255587ce93ea9770552f42566d56070f68ad1661709c030fbd76"


class SpreadFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture_bytes = FIXTURE_PATH.read_bytes()
        cls.fixture = json.loads(fixture_bytes)
        cls.fixture_hash = hashlib.sha256(fixture_bytes).hexdigest()

    def test_fixture_economic_examples_match_literal_expected_values(self) -> None:
        self.assertEqual(self.fixture_hash, FIXTURE_SHA256)
        self.assertEqual(self.fixture["schema_version"], STRATEGY_SPEC_VERSION)
        for example in self.fixture["economic_examples"]:
            with self.subTest(example=example["id"]):
                history = [
                    Decimal(row["value_bps"])
                    for row in self.fixture["funding_profiles"][example["funding_profile"]]
                    for _ in range(row["count"])
                ]
                expected = example["expected"]
                swap_spread = fixed_swap_spread_bps(
                    Decimal(example["cms_bps"]), Decimal(example["cmt_bps"])
                )
                funding = expected_funding_bps(history)
                gross = gross_excess_spread_bps(swap_spread, funding)
                costs = example["round_trip_costs_usd"]
                cost_buffer = directional_cost_buffer_bps(
                    *(Decimal(costs[name]) for name in (
                        "swap_bid_ask", "treasury_bid_ask", "commission_exchange",
                        "slippage", "roll", "financing_not_in_funding",
                    )),
                    Decimal(example["target_swap_leg_dv01_usd_per_bp"]),
                )
                self.assertEqual(swap_spread, Decimal(expected["swap_spread_bps"]))
                self.assertEqual(funding, Decimal(expected["funding_expectation_bps"]))
                self.assertEqual(gross, Decimal(expected["gross_opportunity_bps"]))
                self.assertEqual(
                    cost_buffer * Decimal(example["target_swap_leg_dv01_usd_per_bp"]),
                    Decimal(expected["round_trip_cost_usd"]),
                )
                self.assertEqual(cost_buffer, Decimal(expected["round_trip_cost_bps"]))
                self.assertEqual(
                    net_opportunity_bps(TradeDirection(example["direction"]), gross, cost_buffer),
                    Decimal(expected["net_directional_opportunity_bps"]),
                )

    def test_fixture_quote_conventions_and_treasury_endpoints_are_exact(self) -> None:
        self.assertEqual(self.fixture_hash, FIXTURE_SHA256)
        for convention in self.fixture["quote_conventions"].values():
            with self.subTest(convention=convention["quote_format"]):
                self.assertEqual(
                    tick_value_usd(
                        Decimal(convention["minimum_increment_points"]),
                        Decimal(convention["multiplier_usd_per_point"]),
                    ),
                    Decimal(convention["tick_value_usd"]),
                )
        for example in self.fixture["pnl_examples"][:2]:
            for leg in example["legs"]:
                if "start_source_quote" in leg:
                    for endpoint in ("start", "end"):
                        quote = leg[f"{endpoint}_source_quote"]
                        self.assertEqual(
                            treasury_fractional_quote_to_points(
                                quote["whole_points"], quote["thirty_seconds"],
                                quote["eighths_of_32nd"],
                            ),
                            Decimal(leg[f"{endpoint}_price"]),
                        )


class SpreadBoundaryTests(unittest.TestCase):
    def test_unit_and_spread_conversions(self) -> None:
        self.assertEqual(rate_decimal_to_bps(Decimal("0.045")), Decimal("450"))
        self.assertEqual(rate_decimal_to_bps(Decimal("-0.001")), Decimal("-10"))
        self.assertEqual(
            treasury_fractional_quote_to_points(101, 31, 4), Decimal("101.984375")
        )
        self.assertEqual(treasury_fractional_quote_to_points(0, 0, 0), Decimal("0"))
        self.assertEqual(treasury_fractional_quote_to_points(0, 31, 7), Decimal("0.99609375"))
        self.assertEqual(tick_value_usd(Decimal("0.0025"), Decimal("1000")), Decimal("2.5"))
        self.assertEqual(fixed_swap_spread_bps(Decimal("450"), Decimal("420")), Decimal("30"))
        self.assertEqual(funding_spread_bps(Decimal("5"), Decimal("2")), Decimal("3"))
        self.assertEqual(
            gross_excess_spread_bps(Decimal("30"), Decimal("5")), Decimal("25")
        )

    def test_funding_history_warmup_and_tail_boundaries(self) -> None:
        self.assertIsNone(expected_funding_bps([Decimal("5")] * 39))
        self.assertEqual(expected_funding_bps([Decimal("5")] * 40), Decimal("5"))
        self.assertEqual(
            expected_funding_bps([Decimal("999")] + [Decimal("5")] * 60), Decimal("5")
        )
        self.assertEqual(expected_funding_bps([Decimal("5")] * 61), Decimal("5"))

    def test_net_opportunity_is_directionally_symmetric_and_costs_can_be_zero(self) -> None:
        self.assertEqual(
            directional_cost_buffer_bps(
                Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
                Decimal("0"), Decimal("0"), Decimal("1000"),
            ),
            Decimal("0"),
        )
        self.assertEqual(
            net_opportunity_bps(TradeDirection.TRADITIONAL, Decimal("25"), Decimal("1")),
            Decimal("24"),
        )
        self.assertEqual(
            net_opportunity_bps(TradeDirection.REVERSE, Decimal("-25"), Decimal("1")),
            Decimal("24"),
        )

    def test_invalid_scalars_return_none(self) -> None:
        invalid = (None, 1.0, Decimal("NaN"), Decimal("Infinity"))
        for value in invalid:
            with self.subTest(function="rate", value=value):
                self.assertIsNone(rate_decimal_to_bps(value))
            with self.subTest(function="tick", value=value):
                self.assertIsNone(tick_value_usd(value, Decimal("1")))
                self.assertIsNone(tick_value_usd(Decimal("1"), value))
            for function in (fixed_swap_spread_bps, funding_spread_bps, gross_excess_spread_bps):
                with self.subTest(function=function.__name__, value=value):
                    self.assertIsNone(function(value, Decimal("1")))
                    self.assertIsNone(function(Decimal("1"), value))
            with self.subTest(function="cost", value=value):
                self.assertIsNone(
                    directional_cost_buffer_bps(
                        value, Decimal("0"), Decimal("0"), Decimal("0"),
                        Decimal("0"), Decimal("0"), Decimal("1"),
                    )
                )
            with self.subTest(function="net", value=value):
                self.assertIsNone(
                    net_opportunity_bps(TradeDirection.TRADITIONAL, value, Decimal("0"))
                )
                self.assertIsNone(
                    net_opportunity_bps(TradeDirection.TRADITIONAL, Decimal("1"), value)
                )

    def test_invalid_funding_histories_return_none(self) -> None:
        self.assertIsNone(expected_funding_bps(None))
        self.assertIsNone(expected_funding_bps("5"))
        self.assertIsNone(expected_funding_bps([Decimal("5")] * 39 + [1.0]))
        self.assertIsNone(expected_funding_bps([Decimal("5")] * 39 + [Decimal("NaN")]))
        self.assertIsNone(expected_funding_bps([Decimal("5")] * 39 + [Decimal("Infinity")]))

    def test_invalid_quote_fields_return_none(self) -> None:
        for quote in ((-1, 0, 0), (0, -1, 0), (0, 32, 0), (0, 0, -1), (0, 0, 8),
                      (True, 0, 0), (0, True, 0), (0, 0, True), (0.0, 0, 0)):
            with self.subTest(quote=quote):
                self.assertIsNone(treasury_fractional_quote_to_points(*quote))

    def test_negative_costs_and_nonpositive_cost_base_return_none(self) -> None:
        valid_costs = (Decimal("1"),) * 6
        for index in range(6):
            costs = list(valid_costs)
            costs[index] = Decimal("-1")
            with self.subTest(cost=index):
                self.assertIsNone(directional_cost_buffer_bps(*costs, Decimal("1")))
        for base in (Decimal("0"), Decimal("-1"), None, 1.0, Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(base=base):
                self.assertIsNone(directional_cost_buffer_bps(*valid_costs, base))

    def test_invalid_directions_return_none(self) -> None:
        for direction in (TradeDirection.FLAT, 1, -1, None):
            with self.subTest(direction=direction):
                self.assertIsNone(net_opportunity_bps(direction, Decimal("1"), Decimal("0")))

    def test_decimal_context_is_unchanged(self) -> None:
        before = getcontext().copy()
        self.assertEqual(expected_funding_bps([Decimal("1")] * 40), Decimal("1"))
        self.assertEqual(
            directional_cost_buffer_bps(
                Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"),
                Decimal("1"), Decimal("1"), Decimal("7"),
            ),
            Decimal("0.85714285714285714285714285714285714285714285714286"),
        )
        after = getcontext()
        self.assertEqual(
            (after.prec, after.rounding, after.Emin, after.Emax, after.capitals, after.clamp),
            (before.prec, before.rounding, before.Emin, before.Emax, before.capitals, before.clamp),
        )
        self.assertEqual(dict(after.flags), dict(before.flags))
        self.assertEqual(dict(after.traps), dict(before.traps))


if __name__ == "__main__":
    unittest.main()
