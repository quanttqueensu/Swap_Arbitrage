"""Causal z-score contract tests.

Mutation map: each test names the production change it is intended to catch.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, FloatOperation, Inexact, ROUND_UP, getcontext, setcontext
from hashlib import sha256
import json
from pathlib import Path
import unittest

from strategy import SpreadObservation, causal_zscore


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "strategy_equation_examples.json"
FIXTURE_SHA256 = "3fb9da5fc9ad255587ce93ea9770552f42566d56070f68ad1661709c030fbd76"
BASE_TIME = datetime(2026, 8, 3, tzinfo=timezone.utc)


def observation(maturity, when, gross, *, z_score=None, count=252,
                quality=True, fresh=True):
    return SpreadObservation(
        maturity=maturity,
        observation_time_utc=when,
        fixed_swap_spread_bps=Decimal("0"),
        expected_funding_spread_bps=Decimal("0"),
        gross_excess_spread_bps=gross,
        traditional_cost_buffer_bps=Decimal("0"),
        reverse_cost_buffer_bps=Decimal("0"),
        traditional_net_opportunity_bps=gross,
        reverse_net_opportunity_bps=-gross,
        z_score=z_score,
        observation_count=count,
        source_quality_ok=quality,
        is_fresh=fresh,
    )


def history(values, maturity="2Y", start=BASE_TIME):
    return [observation(maturity, start + timedelta(minutes=index), value)
            for index, value in enumerate(values)]


def context_snapshot(context):
    return (
        context.prec,
        context.rounding,
        context.Emin,
        context.Emax,
        context.capitals,
        context.clamp,
        tuple(context.flags.items()),
        tuple(context.traps.items()),
    )


class CausalZScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_bytes = FIXTURE_PATH.read_bytes()
        assert sha256(fixture_bytes).hexdigest() == FIXTURE_SHA256
        cls.fixture = json.loads(fixture_bytes)
        assert cls.fixture["schema_version"] == "p10.strategy-equations.v1"
        cls.profiles = {
            name: tuple(
                Decimal(segment["value_bps"])
                for segment in profile
                for _ in range(segment["count"])
            )
            for name, profile in cls.fixture["gross_history_profiles"].items()
        }
        assert all(len(profile) == 252 for profile in cls.profiles.values())

    def current(self, gross, maturity="2Y"):
        return observation(maturity, BASE_TIME + timedelta(minutes=252), Decimal(gross))

    # Mutation caught: changing the calculation to a population denominator or wrong history values.
    def test_fixture_economic_examples_have_literal_zscores(self):
        expected_scores = {
            "traditional_2y": Decimal("2"),
            "traditional_5y": Decimal("3"),
            "reverse_2y": Decimal("-2"),
            "reverse_5y": Decimal("-2.6"),
        }
        for example in self.fixture["economic_examples"]:
            with self.subTest(example=example["id"]):
                prior = history(self.profiles[example["gross_history_profile"]], example["maturity"])
                current = self.current(
                    example["expected"]["gross_opportunity_bps"], example["maturity"]
                )
                self.assertEqual(causal_zscore(current, prior), expected_scores[example["id"]])

    # Mutation caught: relaxing or changing the exact 252-observation window.
    def test_requires_exactly_252_prior_observations(self):
        values = self.profiles["mean_0_sd_5_252"]
        self.assertIsNone(causal_zscore(self.current("10"), history(values[:-1])))
        self.assertEqual(causal_zscore(self.current("10"), tuple(history(values))), Decimal("2"))
        self.assertIsNone(causal_zscore(self.current("10"), history(values + (Decimal("0"),))))

    # Mutation caught: returning a z-score when all prior gross spreads are identical.
    def test_zero_variance_returns_none(self):
        self.assertIsNone(causal_zscore(self.current("7"), history((Decimal("5"),) * 252)))

    # Mutation caught: adding current gross spread to the historical mean or variance.
    def test_excludes_current_from_the_history_statistics(self):
        prior = history(self.profiles["mean_0_sd_5_252"])
        self.assertEqual(causal_zscore(self.current("10"), prior), Decimal("2"))

    # Mutation caught: accepting unordered, duplicate, current, or future history timestamps.
    def test_rejects_noncausal_observation_times(self):
        prior = history(self.profiles["mean_0_sd_5_252"])
        current = self.current("10")
        cases = {
            "reversed": prior[:1] + [prior[2], prior[1]] + prior[3:],
            "duplicate": prior[:2] + [replace(prior[2], observation_time_utc=prior[1].observation_time_utc)] + prior[3:],
            "current": prior[:-1] + [replace(prior[-1], observation_time_utc=current.observation_time_utc)],
            "future": prior[:-1] + [replace(prior[-1], observation_time_utc=current.observation_time_utc + timedelta(minutes=1))],
        }
        for name, invalid_prior in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(causal_zscore(current, invalid_prior))

    # Mutation caught: assuming model construction prevents corrupted naive or non-UTC timestamps.
    def test_rejects_corrupted_non_utc_timestamps(self):
        prior = history(self.profiles["mean_0_sd_5_252"])
        current = self.current("10")
        naive_current = replace(current)
        object.__setattr__(naive_current, "observation_time_utc", current.observation_time_utc.replace(tzinfo=None))
        non_utc_current = replace(current)
        object.__setattr__(non_utc_current, "observation_time_utc", current.observation_time_utc.replace(tzinfo=timezone(-timedelta(hours=1))))
        naive_prior = history(self.profiles["mean_0_sd_5_252"])
        object.__setattr__(naive_prior[-1], "observation_time_utc", naive_prior[-1].observation_time_utc.replace(tzinfo=None))
        non_utc_prior = history(self.profiles["mean_0_sd_5_252"])
        object.__setattr__(non_utc_prior[0], "observation_time_utc", non_utc_prior[0].observation_time_utc.replace(tzinfo=timezone(timedelta(hours=1))))
        for name, invalid_current, invalid_prior in (
            ("naive_current", naive_current, prior),
            ("non_utc_current", non_utc_current, prior),
            ("naive_prior", current, naive_prior),
            ("non_utc_prior", current, non_utc_prior),
        ):
            with self.subTest(case=name):
                try:
                    result = causal_zscore(invalid_current, invalid_prior)
                except TypeError as error:
                    self.fail(f"causal_zscore raised {error!r}")
                self.assertIsNone(result)

    # Mutation caught: calculating across maturities or using a poor-quality source row.
    def test_rejects_mismatched_maturity_and_poor_quality_history(self):
        prior = history(self.profiles["mean_0_sd_5_252"])
        current = self.current("10")
        self.assertIsNone(causal_zscore(current, prior[:-1] + [replace(prior[-1], maturity="5Y")]))
        self.assertIsNone(causal_zscore(current, prior[:-1] + [replace(prior[-1], source_quality_ok=False)]))
        corrupted_quality = replace(prior[-1])
        object.__setattr__(corrupted_quality, "source_quality_ok", 1)
        self.assertIsNone(causal_zscore(current, prior[:-1] + [corrupted_quality]))

    # Mutation caught: iterating arbitrary objects, strings, or non-observation collection members.
    def test_rejects_invalid_current_collections_and_members(self):
        prior = history(self.profiles["mean_0_sd_5_252"])
        current = self.current("10")
        self.assertIsNone(causal_zscore(object(), prior))
        self.assertIsNone(causal_zscore(current, object()))
        self.assertIsNone(causal_zscore(current, iter(prior)))
        self.assertIsNone(causal_zscore(current, "not a history"))
        self.assertIsNone(causal_zscore(current, prior[:-1] + [object()]))

    # Mutation caught: retaining or accepting later observations after a valid result is calculated.
    def test_later_future_observation_cannot_change_saved_result(self):
        prior = history(self.profiles["mean_0_sd_5_252"])
        current = self.current("10")
        saved = causal_zscore(current, prior)
        later = observation("2Y", current.observation_time_utc + timedelta(minutes=1), Decimal("999"))
        future_prior = prior[:-1] + [later]
        self.assertIsNone(causal_zscore(current, future_prior))
        later = replace(later, gross_excess_spread_bps=Decimal("-999"))
        future_prior[-1] = later
        self.assertIsNone(causal_zscore(current, future_prior))
        self.assertEqual(saved, Decimal("2"))
        self.assertEqual(causal_zscore(current, prior), Decimal("2"))

    # Mutation caught: using caller precision or leaking Decimal context changes to the caller.
    def test_uses_precision_50_without_changing_caller_decimal_context(self):
        original = getcontext().copy()
        constrained = original.copy()
        constrained.prec = 2
        constrained.rounding = ROUND_UP
        constrained.Emin = -999
        constrained.Emax = 999
        constrained.capitals = 0
        constrained.clamp = 1
        constrained.clear_flags()
        constrained.flags[Inexact] = True
        constrained.traps[FloatOperation] = True
        expected = context_snapshot(constrained)
        try:
            setcontext(constrained.copy())
            aliased = getcontext()
            aliased.prec = 50
            self.assertEqual(getcontext().prec, aliased.prec)
            self.assertNotEqual(context_snapshot(getcontext()), expected)
            setcontext(constrained.copy())
            result = causal_zscore(self.current("10"), history(self.profiles["mean_0_sd_5_252"]))
            self.assertEqual(result, Decimal("2"))
            self.assertEqual(context_snapshot(getcontext()), expected)
        finally:
            setcontext(original)


if __name__ == "__main__":
    unittest.main()
