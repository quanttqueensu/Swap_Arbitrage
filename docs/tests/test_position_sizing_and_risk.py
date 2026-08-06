from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext, setcontext
import unittest

from strategy import TradeDirection
from strategy.position_sizing import (
    SIZING_RISK_VERSION,
    build_target_position,
    liquidity_scale,
    scaled_target_dv01,
    signal_strength_scale,
    volatility_scale,
)


D = Decimal
DECISION = datetime(2026, 1, 5, 21, 0, tzinfo=timezone.utc)


def prior_vols(value=D("0.8")):
    return tuple(
        (DECISION - timedelta(days=63 - index), value)
        for index in range(63)
    )


def target_kwargs(**overrides):
    values = dict(
        maturity="2Y",
        swap_instrument_id="YITH27",
        treasury_instrument_id="ZTH27",
        direction=TradeDirection.TRADITIONAL,
        base_target_dv01_usd_per_bp=D("1000"),
        decision_time_utc=DECISION,
        current_realized_vol=D("1"),
        prior_realized_vols=prior_vols(D("1")),
        z_score=D("2"),
        swap_available_contracts=100,
        treasury_available_contracts=100,
        swap_dv01_usd_per_bp=D("100"),
        treasury_dv01_usd_per_bp=D("950"),
        current_swap_quantity_contracts=0,
        current_treasury_quantity_contracts=0,
        max_swap_contracts=0,
        max_treasury_contracts=0,
        available_gross_dv01_usd_per_bp=D("10000"),
        expected_cost_usd=D("0"),
    )
    values.update(overrides)
    return values


class ScaleTests(unittest.TestCase):
    def test_frozen_version_and_hand_examples(self):
        prior = prior_vols()
        self.assertEqual(SIZING_RISK_VERSION, "p33.position-sizing-risk.v1")
        self.assertEqual(volatility_scale(DECISION, D("1"), prior), D("0.8"))
        self.assertEqual(volatility_scale(DECISION, D("0.5"), prior), D("1"))
        self.assertEqual(signal_strength_scale(D("0")), D("0"))
        self.assertEqual(signal_strength_scale(D("1")), D("0.5"))
        self.assertEqual(signal_strength_scale(D("2")), D("1"))
        self.assertEqual(signal_strength_scale(D("-3")), D("1"))
        self.assertEqual(liquidity_scale(10, -4, 5, 4), D("0.5"))
        self.assertEqual(
            scaled_target_dv01(D("3000"), D("0.8"), D("0.5"), D("0.5")),
            D("600"),
        )

    def test_volatility_requires_exact_causal_window(self):
        valid = tuple(
            (DECISION - timedelta(days=63 - index), D(index + 1))
            for index in range(63)
        )
        self.assertIsNotNone(volatility_scale(DECISION, D("64"), valid))
        for invalid in (valid[:-1], valid + (D("64"),), "not-a-sequence"):
            self.assertIsNone(volatility_scale(DECISION, D("64"), invalid))
        future = valid[:-1] + ((DECISION, D("63")),)
        reversed_pair = valid[:30] + (valid[31], valid[30]) + valid[32:]
        naive = valid[:-1] + ((datetime(2026, 1, 4, 21, 0), D("63")),)
        for invalid in (future, reversed_pair, naive):
            self.assertIsNone(volatility_scale(DECISION, D("64"), invalid))
        for invalid in (D("0"), D("-1"), D("NaN"), 1, True):
            self.assertIsNone(volatility_scale(DECISION, invalid, valid))

    def test_zero_interior_and_full_scale_boundaries(self):
        self.assertEqual(liquidity_scale(10, -4, 0, 4), D("0"))
        self.assertEqual(liquidity_scale(10, -4, 10, 4), D("1"))
        for invalid in ((0, -4, 1, 1), (10, 0, 1, 1), (10, -4, -1, 1)):
            self.assertIsNone(liquidity_scale(*invalid))
        self.assertIsNone(scaled_target_dv01(D("3000"), D("1.1"), D("1"), D("1")))

    def test_public_scales_preserve_complete_decimal_context(self):
        original = getcontext().copy()
        try:
            context = getcontext()
            context.prec = 2
            context.rounding = "ROUND_DOWN"
            before = context.copy()
            self.assertEqual(
                volatility_scale(DECISION, D("3"), prior_vols(D("2"))),
                D("0.66666666666666666666666666666666666666666666666667"),
            )
            after = getcontext()
            self.assertEqual(after.prec, before.prec)
            self.assertEqual(after.rounding, before.rounding)
            self.assertEqual(after.traps, before.traps)
            self.assertEqual(after.flags, before.flags)
        finally:
            setcontext(original)


class TargetPositionTests(unittest.TestCase):
    def test_hand_checked_traditional_basket(self):
        target = build_target_position(**target_kwargs())
        self.assertIsNotNone(target)
        self.assertEqual(target.swap_quantity_contracts, 10)
        self.assertEqual(target.treasury_quantity_contracts, -1)
        self.assertEqual(target.target_dv01_usd_per_bp, D("1000"))
        self.assertEqual(target.gross_dv01_usd_per_bp, D("1950"))
        self.assertEqual(target.residual_net_dv01_usd_per_bp, D("-50"))
        self.assertEqual(target.expected_turnover_contracts, 11)
        self.assertEqual(target.cap_diagnostic, "within_capacity")

    def test_capacity_limits_scale_instead_of_overallocating(self):
        uncapped = build_target_position(
            **target_kwargs(base_target_dv01_usd_per_bp=D("3000"))
        )
        capped = build_target_position(
            **target_kwargs(
                base_target_dv01_usd_per_bp=D("3000"), max_swap_contracts=10,
            )
        )
        self.assertIsNotNone(uncapped)
        self.assertIsNotNone(capped)
        self.assertLessEqual(abs(capped.swap_quantity_contracts), 10)
        self.assertLess(
            capped.gross_dv01_usd_per_bp, uncapped.gross_dv01_usd_per_bp
        )
        self.assertEqual(capped.cap_diagnostic, "scaled_to_capacity")

    def test_tighter_capacity_never_increases_risk(self):
        gross_values = []
        for capacity in (D("5000"), D("3000"), D("2000"), D("1000")):
            target = build_target_position(
                **target_kwargs(
                    base_target_dv01_usd_per_bp=D("3000"),
                    available_gross_dv01_usd_per_bp=capacity,
                )
            )
            gross_values.append(
                D("0") if target is None else target.gross_dv01_usd_per_bp
            )
        self.assertEqual(gross_values, sorted(gross_values, reverse=True))

    def test_residual_boundary_and_zero_risk_fail_closed(self):
        self.assertIsNotNone(build_target_position(**target_kwargs()))
        self.assertIsNone(
            build_target_position(
                **target_kwargs(treasury_dv01_usd_per_bp=D("949.9"))
            )
        )
        self.assertIsNone(
            build_target_position(**target_kwargs(swap_available_contracts=0))
        )
        self.assertIsNone(
            build_target_position(
                **target_kwargs(available_gross_dv01_usd_per_bp=D("0"))
            )
        )

    def test_turnover_uses_current_and_target_quantities(self):
        target = build_target_position(
            **target_kwargs(
                current_swap_quantity_contracts=4,
                current_treasury_quantity_contracts=-1,
            )
        )
        self.assertEqual(target.expected_turnover_contracts, 6)

    def test_residual_only_scaling_is_not_a_capacity_diagnostic(self):
        target = build_target_position(
            **target_kwargs(
                base_target_dv01_usd_per_bp=D("300"),
                swap_dv01_usd_per_bp=D("100"),
                treasury_dv01_usd_per_bp=D("63.4"),
                swap_available_contracts=10000,
                treasury_available_contracts=10000,
                available_gross_dv01_usd_per_bp=D("10000000"),
            )
        )
        self.assertIsNotNone(target)
        self.assertEqual(target.target_dv01_usd_per_bp, D("200"))
        self.assertEqual(target.cap_diagnostic, "within_capacity")
