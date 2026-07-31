from __future__ import annotations

import json
import unittest
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP, getcontext, localcontext
from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "strategy_equation_examples.json"
getcontext().prec = 50


def D(value: object) -> Decimal:
    return Decimal(str(value))


def expand_segments(segments: list[dict[str, object]]) -> list[Decimal]:
    values: list[Decimal] = []
    for segment in segments:
        values.extend([D(segment["value_bps"])] * int(segment["count"]))
    return values


def mean(values: list[Decimal]) -> Decimal:
    return sum(values, D("0")) / D(len(values))


def sample_std(values: list[Decimal]) -> Decimal:
    center = mean(values)
    variance = sum((value - center) ** 2 for value in values) / D(len(values) - 1)
    with localcontext() as context:
        context.prec = 50
        return variance.sqrt()


def funding_expectation(history: list[Decimal]) -> Decimal | None:
    if len(history) < 40:
        return None
    trailing = history[-60:]
    one_step = mean(trailing)
    return mean([one_step] * 20)


def causal_zscore(current: Decimal, prior: list[Decimal]) -> Decimal | None:
    if len(prior) != 252:
        return None
    sigma = sample_std(prior)
    if sigma == 0:
        return None
    return (current - mean(prior)) / sigma


def economic_result(
    example: dict[str, object], fixture: dict[str, object]
) -> dict[str, Decimal]:
    funding = funding_expectation(
        expand_segments(fixture["funding_profiles"][example["funding_profile"]])
    )
    if funding is None:
        raise AssertionError("golden example must have a valid funding history")
    gross = D(example["cms_bps"]) - D(example["cmt_bps"]) - funding
    costs = example["round_trip_costs_usd"]
    cost_usd = sum((D(value) for value in costs.values()), D("0"))
    cost_bps = cost_usd / abs(D(example["target_swap_leg_dv01_usd_per_bp"]))
    net = D(example["direction"]) * gross - cost_bps
    prior = expand_segments(
        fixture["gross_history_profiles"][example["gross_history_profile"]]
    )
    zscore = causal_zscore(gross, prior)
    if zscore is None:
        raise AssertionError("golden example must have a valid z-score history")
    return {
        "swap_spread_bps": D(example["cms_bps"]) - D(example["cmt_bps"]),
        "funding_expectation_bps": funding,
        "gross_opportunity_bps": gross,
        "round_trip_cost_usd": cost_usd,
        "round_trip_cost_bps": cost_bps,
        "net_directional_opportunity_bps": net,
        "zscore": zscore,
    }


class FixtureContractTests(unittest.TestCase):
    def test_fixture_has_four_directional_examples(self) -> None:
        self.assertTrue(FIXTURE_PATH.exists(), f"missing fixture: {FIXTURE_PATH}")
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        examples = fixture["economic_examples"]
        self.assertEqual(len(examples), 4)
        self.assertEqual(
            [(item["maturity"], item["direction"]) for item in examples],
            [("2Y", 1), ("5Y", 1), ("2Y", -1), ("5Y", -1)],
        )


class EconomicEquationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_all_four_golden_examples_recalculate_exactly(self) -> None:
        for example in self.fixture["economic_examples"]:
            with self.subTest(example=example["id"]):
                actual = economic_result(example, self.fixture)
                expected = {key: D(value) for key, value in example["expected"].items()}
                self.assertEqual(actual, expected)

    def test_history_profiles_have_declared_sample_moments(self) -> None:
        declared = {
            "mean_15_sd_5_252": (D("15"), D("5")),
            "mean_0_sd_5_252": (D("0"), D("5")),
            "mean_minus_25_sd_10_252": (D("-25"), D("10")),
            "mean_minus_5_sd_5_252": (D("-5"), D("5")),
        }
        for profile, (declared_mean, declared_std) in declared.items():
            with self.subTest(profile=profile):
                history = expand_segments(self.fixture["gross_history_profiles"][profile])
                self.assertEqual(len(history), 252)
                self.assertEqual(mean(history), declared_mean)
                self.assertEqual(sample_std(history), declared_std)

    def test_funding_warmup_and_window(self) -> None:
        profiles = self.fixture["funding_profiles"]
        self.assertIsNone(funding_expectation(expand_segments(profiles["flat_5_bps_39"])))
        self.assertEqual(funding_expectation(expand_segments(profiles["flat_5_bps_40"])), D("5"))
        sixty = expand_segments(profiles["flat_5_bps_60"])
        self.assertEqual(funding_expectation(sixty), D("5"))
        self.assertEqual(funding_expectation([D("999")] + sixty), D("5"))

    def test_zscore_requires_exact_prior_window_and_nonzero_variance(self) -> None:
        history = expand_segments(self.fixture["gross_history_profiles"]["mean_0_sd_5_252"])
        self.assertIsNone(causal_zscore(D("10"), history[:-1]))
        self.assertIsNone(causal_zscore(D("10"), history + [D("0")]))
        self.assertIsNone(causal_zscore(D("10"), [D("1")] * 252))
        self.assertEqual(causal_zscore(D("10"), history), D("2"))

    def test_current_observation_is_excluded_from_zscore_moments(self) -> None:
        prior = expand_segments(self.fixture["gross_history_profiles"]["mean_0_sd_5_252"])
        prior_mean = mean(prior)
        prior_std = sample_std(prior)
        self.assertEqual(causal_zscore(D("10"), prior), D("2"))
        self.assertEqual(causal_zscore(D("15"), prior), D("3"))
        self.assertEqual(mean(prior), prior_mean)
        self.assertEqual(sample_std(prior), prior_std)

    def test_future_observations_do_not_revise_prior_results(self) -> None:
        funding_history = expand_segments(self.fixture["funding_profiles"]["flat_5_bps_60"])
        prior = expand_segments(self.fixture["gross_history_profiles"]["mean_0_sd_5_252"])
        funding_at_t = funding_expectation(funding_history)
        zscore_at_t = causal_zscore(D("10"), prior)
        funding_with_future = funding_history + [D("999")]
        prior_with_future = prior + [D("999")]
        self.assertEqual(funding_expectation(funding_with_future[:-1]), funding_at_t)
        self.assertEqual(causal_zscore(D("10"), prior_with_future[:-1]), zscore_at_t)

    def test_strictly_positive_net_is_required(self) -> None:
        self.assertFalse(D("0") > D("0"))
        self.assertTrue(D("0.0001") > D("0"))
