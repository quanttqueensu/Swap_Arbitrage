from decimal import Decimal
import unittest

from strategy.live_signal import LIVE_SIGNAL_STRATEGY_VERSION, LiveSignalResult
from strategy.live_target import MaturityRiskInputs, build_live_target


def signal(maturity: str, state: int, z: str | None, blocked: bool = False):
    return LiveSignalResult(
        maturity=maturity,
        strategy_version=LIVE_SIGNAL_STRATEGY_VERSION,
        snapshot_id=f"snap-{maturity}",
        mid_spread_bps=Decimal("10") if not blocked else None,
        spread_bid_side_bps=Decimal("9") if not blocked else None,
        spread_ask_side_bps=Decimal("11") if not blocked else None,
        historical_mean_bps=Decimal("0"),
        historical_std_bps=Decimal("5"),
        z_score=Decimal(z) if z is not None else None,
        prior_state=0,
        state=state,
        blocked=blocked,
        reason_codes=("blocked",) if blocked else ("within_signal_model",),
    )


def risk(
    *,
    base: str = "3000",
    vol: str = "1",
    swap_dv01: str = "20",
    treasury_dv01: str = "40",
    swap_cap: int = 0,
    treasury_cap: int = 0,
):
    return MaturityRiskInputs(
        base_target_dv01=Decimal(base),
        vol_scale=Decimal(vol),
        swap_dv01_per_contract=Decimal(swap_dv01),
        treasury_dv01_per_contract=Decimal(treasury_dv01),
        max_swap_contracts=swap_cap,
        max_treasury_contracts=treasury_cap,
    )


class LiveTargetTests(unittest.TestCase):
    def test_active_short_signal_sizes_swap_and_treasury_hedge(self) -> None:
        target = build_live_target(
            signals={"2Y": signal("2Y", -1, "2.5")},
            risk_inputs={"2Y": risk()},
        )
        two = target.maturities["2Y"]
        self.assertEqual(two.signal_strength_scale, Decimal("1"))
        self.assertEqual(two.target_dv01, Decimal("3000"))
        self.assertEqual(two.swap_quantity, -150)
        self.assertEqual(two.treasury_quantity, 75)
        self.assertEqual(two.residual_dv01, Decimal("0"))
        self.assertFalse(two.blocked)

    def test_held_signal_scales_by_z_and_volatility(self) -> None:
        target = build_live_target(
            signals={"2Y": signal("2Y", -1, "1.0")},
            risk_inputs={"2Y": risk(vol="0.5")},
        )
        two = target.maturities["2Y"]
        self.assertEqual(two.signal_strength_scale, Decimal("0.5"))
        self.assertEqual(two.target_dv01, Decimal("750.00"))
        self.assertEqual(two.swap_quantity, -38)
        self.assertEqual(two.treasury_quantity, 19)

    def test_blocked_signal_has_zero_hypothetical_exposure(self) -> None:
        target = build_live_target(
            signals={"2Y": signal("2Y", 0, None, blocked=True)},
            risk_inputs={"2Y": risk()},
        )
        two = target.maturities["2Y"]
        self.assertEqual(two.swap_quantity, 0)
        self.assertEqual(two.treasury_quantity, 0)
        self.assertEqual(two.target_dv01, Decimal("0"))
        self.assertTrue(two.blocked)

    def test_target_below_minimum_dv01_is_zero(self) -> None:
        target = build_live_target(
            signals={"2Y": signal("2Y", 1, "0.6")},
            risk_inputs={"2Y": risk(base="300", vol="1")},
            min_target_dv01=Decimal("100"),
        )
        self.assertEqual(target.maturities["2Y"].target_dv01, Decimal("0"))

    def test_gross_dv01_is_scaled_to_portfolio_cap(self) -> None:
        target = build_live_target(
            signals={
                "2Y": signal("2Y", 1, "2.5"),
                "5Y": signal("5Y", -1, "2.5"),
            },
            risk_inputs={
                "2Y": risk(base="6000", swap_dv01="20", treasury_dv01="40"),
                "5Y": risk(base="6000", swap_dv01="50", treasury_dv01="50"),
            },
            max_gross_dv01=Decimal("10000"),
        )
        self.assertLessEqual(target.gross_target_dv01, Decimal("10000"))
        self.assertLess(target.dv01_cap_scale, Decimal("1"))
        self.assertFalse(target.blocked)

    def test_rounding_cannot_bypass_gross_dv01_cap(self) -> None:
        target = build_live_target(
            signals={"2Y": signal("2Y", 1, "2")},
            risk_inputs={
                "2Y": risk(base="100", swap_dv01="160", treasury_dv01="160")
            },
            min_target_dv01=Decimal("0"),
            max_gross_dv01=Decimal("100"),
            max_net_dv01=Decimal("1"),
        )
        two = target.maturities["2Y"]
        actual_gross = (
            two.signed_swap_dv01.copy_abs()
            + two.signed_treasury_dv01.copy_abs()
        )
        self.assertLessEqual(actual_gross, Decimal("100"))
        self.assertLessEqual(target.gross_target_dv01, Decimal("100"))

    def test_contract_cap_is_applied_before_hedge(self) -> None:
        target = build_live_target(
            signals={"2Y": signal("2Y", 1, "2.5")},
            risk_inputs={"2Y": risk(swap_cap=10, treasury_cap=10)},
        )
        two = target.maturities["2Y"]
        self.assertEqual(two.swap_quantity, 10)
        self.assertEqual(two.treasury_quantity, -5)
        self.assertTrue(two.swap_contract_cap_hit)

    def test_net_dv01_breach_blocks_entire_target(self) -> None:
        target = build_live_target(
            signals={"2Y": signal("2Y", 1, "2.5")},
            risk_inputs={"2Y": risk(swap_dv01="20", treasury_dv01="37")},
            max_net_dv01=Decimal("1"),
        )
        self.assertTrue(target.blocked)
        self.assertIn("portfolio_net_dv01_limit", target.reason_codes)
        self.assertEqual(target.maturities["2Y"].swap_quantity, 0)
        self.assertEqual(target.maturities["2Y"].treasury_quantity, 0)


if __name__ == "__main__":
    unittest.main()
