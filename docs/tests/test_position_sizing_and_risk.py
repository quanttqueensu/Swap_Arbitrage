from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext, setcontext
import unittest

from strategy.position_sizing import (
    SIZING_RISK_VERSION,
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
