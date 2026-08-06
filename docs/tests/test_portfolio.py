from decimal import Decimal, getcontext, setcontext
import unittest

from strategy.models import TargetPosition
from strategy.portfolio import portfolio_dv01, select_portfolio_targets


D = Decimal


def target(maturity, gross, net):
    return TargetPosition(
        maturity=maturity,
        swap_instrument_id=f"swap-{maturity}",
        treasury_instrument_id=f"treasury-{maturity}",
        swap_quantity_contracts=1,
        treasury_quantity_contracts=-1,
        target_dv01_usd_per_bp=D("1"),
        gross_dv01_usd_per_bp=D(gross),
        residual_net_dv01_usd_per_bp=D(net),
        expected_turnover_contracts=2,
        expected_cost_usd=D("0"),
        rounding_diagnostic="minimum_residual",
        cap_diagnostic="within_capacity",
    )


class PortfolioDv01Tests(unittest.TestCase):
    # Mutation caught: omitting gross or residual-net DV01 from the portfolio total.
    def test_sums_hand_checked_gross_and_net_dv01(self):
        targets = (target("2Y", "300", "-20"), target("5Y", "400", "50"))
        self.assertEqual(portfolio_dv01(targets), (D("700"), D("30")))

    # Mutation caught: accepting duplicate maturities, non-target objects, or corrupted target data.
    def test_rejects_duplicate_and_malformed_target_inputs(self):
        valid = target("2Y", "300", "-20")
        corrupted = target("5Y", "400", "50")
        object.__setattr__(corrupted, "gross_dv01_usd_per_bp", D("NaN"))
        for targets in (
            (valid, target("2Y", "400", "50")),
            (valid, object()),
            (corrupted,),
            "not targets",
        ):
            with self.subTest(targets=targets):
                self.assertIsNone(portfolio_dv01(targets))

    # Mutation caught: performing aggregation in, or changing, the caller Decimal context.
    def test_preserves_the_callers_decimal_context(self):
        original = getcontext().copy()
        try:
            context = getcontext()
            context.prec = 2
            context.rounding = "ROUND_DOWN"
            before = context.copy()
            targets = (target("2Y", "1.234", "-0.123"), target("5Y", "2.345", "0.456"))
            self.assertEqual(portfolio_dv01(targets), (D("3.579"), D("0.333")))
            after = getcontext()
            self.assertEqual(after.prec, before.prec)
            self.assertEqual(after.rounding, before.rounding)
            self.assertEqual(after.traps, before.traps)
            self.assertEqual(after.flags, before.flags)
        finally:
            setcontext(original)


class PortfolioSelectionTests(unittest.TestCase):
    # Mutation caught: iterating target input order instead of the P32 rank order.
    def test_selects_safe_targets_in_rank_order(self):
        two_year = target("2Y", "300", "-20")
        five_year = target("5Y", "400", "50")
        self.assertEqual(
            select_portfolio_targets(("5Y", "2Y"), (two_year, five_year), D("1000"), D("100")),
            (five_year, two_year),
        )

    # Mutation caught: stopping after a gross-limit breach instead of skipping it.
    def test_skips_a_gross_breach_and_continues_down_the_rank(self):
        targets = (
            target("2Y", "700", "0"),
            target("5Y", "400", "0"),
            target("10Y", "300", "0"),
        )
        self.assertEqual(
            select_portfolio_targets(("2Y", "5Y", "10Y"), targets, D("1000"), D("100")),
            (targets[0], targets[2]),
        )

    # Mutation caught: checking signed net DV01 instead of its absolute value.
    def test_skips_an_absolute_net_dv01_breach_and_continues_down_the_rank(self):
        targets = (
            target("2Y", "300", "-80"),
            target("5Y", "300", "-30"),
            target("10Y", "300", "20"),
        )
        self.assertEqual(
            select_portfolio_targets(("2Y", "5Y", "10Y"), targets, D("1000"), D("100")),
            (targets[0], targets[2]),
        )

    # Mutation caught: accepting duplicate/mismatched rank data, non-finite limits, or non-TargetPosition values.
    def test_rejects_malformed_rank_target_and_limit_inputs(self):
        two_year = target("2Y", "300", "0")
        five_year = target("5Y", "400", "0")
        cases = (
            (("2Y", "2Y"), (two_year,), D("1000"), D("100")),
            (("5Y",), (two_year,), D("1000"), D("100")),
            (("2Y",), (two_year, object()), D("1000"), D("100")),
            (("2Y",), (two_year,), D("NaN"), D("100")),
            (("2Y",), (two_year,), D("1000"), D("-1")),
            (("2Y",), (two_year,), 1000, D("100")),
            ("not ranks", (two_year,), D("1000"), D("100")),
        )
        for case in cases:
            with self.subTest(case=case):
                self.assertIsNone(select_portfolio_targets(*case))

    # Mutation caught: admitting more gross risk after a portfolio limit is tightened.
    def test_tighter_limits_never_increase_selected_gross_risk(self):
        targets = (
            target("2Y", "400", "0"),
            target("5Y", "400", "0"),
            target("10Y", "400", "0"),
        )
        selected_gross = []
        for limit in (D("1200"), D("800"), D("400")):
            selected = select_portfolio_targets(
                ("2Y", "5Y", "10Y"), targets, limit, D("100")
            )
            self.assertIsNotNone(selected)
            selected_gross.append(portfolio_dv01(selected)[0])
        self.assertEqual(selected_gross, [D("1200"), D("800"), D("400")])


if __name__ == "__main__":
    unittest.main()
