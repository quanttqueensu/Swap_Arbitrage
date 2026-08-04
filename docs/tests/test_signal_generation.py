"""Causal z-score contract tests.

Mutation map: each test names the production change it is intended to catch.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, FloatOperation, Inexact, ROUND_UP, getcontext, setcontext
from hashlib import sha256
import json
from pathlib import Path
import unittest

from strategy import (
    NamedValue,
    PositionState,
    SignalDecision,
    SpreadObservation,
    TradeDirection,
    causal_zscore,
    generate_signal_decision,
    rank_opportunities,
    signal_transition,
)


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
        for example in self.fixture["economic_examples"]:
            with self.subTest(example=example["id"]):
                prior = history(self.profiles[example["gross_history_profile"]], example["maturity"])
                current = self.current(
                    example["expected"]["gross_opportunity_bps"], example["maturity"]
                )
                self.assertEqual(
                    causal_zscore(current, prior),
                    Decimal(example["expected"]["zscore"]),
                )

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

    # Mutation caught: trusting exact model identity without revalidating corrupted fields.
    def test_rejects_corrupted_gross_and_timestamp_fields_without_raising(self):
        current = self.current("10")
        cases = []
        for field, value in (
            ("gross_excess_spread_bps", Decimal("NaN")),
            ("gross_excess_spread_bps", 3),
            ("observation_time_utc", "not-a-datetime"),
        ):
            corrupted_current = replace(current)
            object.__setattr__(corrupted_current, field, value)
            cases.append((f"current_{field}_{value!r}", corrupted_current, history(self.profiles["mean_0_sd_5_252"])))

            corrupted_prior = history(self.profiles["mean_0_sd_5_252"])
            object.__setattr__(corrupted_prior[-1], field, value)
            cases.append((f"prior_{field}_{value!r}", current, corrupted_prior))

        for name, corrupted_current, corrupted_prior in cases:
            with self.subTest(case=name):
                try:
                    result = causal_zscore(corrupted_current, corrupted_prior)
                except Exception as error:  # pragma: no cover - an exception is the failure detail
                    self.fail(f"causal_zscore raised {error!r}")
                self.assertIsNone(result)

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


class SignalTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_bytes = FIXTURE_PATH.read_bytes()
        assert sha256(fixture_bytes).hexdigest() == FIXTURE_SHA256
        cls.state_examples = json.loads(fixture_bytes)["state_examples"]

    # Mutation caught: changing frozen transition outcomes or reversal action order.
    def test_fixture_state_examples_have_literal_states_and_actions(self):
        for example in self.state_examples:
            with self.subTest(example=example["id"]):
                self.assertEqual(
                    signal_transition(
                        PositionState(example["position"]),
                        Decimal(example["zscore"]),
                        Decimal(example["traditional_net_bps"]),
                        Decimal(example["reverse_net_bps"]),
                        example["data_ready"],
                        example["risk_flatten"],
                    ),
                    (
                        PositionState(example["expected_position"]),
                        tuple(example["expected_actions"]),
                    ),
                )

    # Mutation caught: moving inclusive entry thresholds or selecting an ineligible side.
    def test_entry_boundaries_are_inclusive_and_require_positive_net(self):
        cases = (
            ("traditional_below", "1.9999", "1", "1", PositionState.FLAT, ()),
            ("traditional_at", "2.0", "0.0001", "1", PositionState.TRADITIONAL, ("enter_traditional",)),
            ("traditional_above", "2.0001", "1", "1", PositionState.TRADITIONAL, ("enter_traditional",)),
            ("reverse_below", "-1.9999", "1", "1", PositionState.FLAT, ()),
            ("reverse_at", "-2.0", "1", "0.0001", PositionState.REVERSE, ("enter_reverse",)),
            ("reverse_above", "-2.0001", "1", "1", PositionState.REVERSE, ("enter_reverse",)),
            ("traditional_zero_net", "2.0", "0", "1", PositionState.FLAT, ()),
            ("reverse_zero_net", "-2.0", "1", "0", PositionState.FLAT, ()),
        )
        for name, z_score, traditional, reverse, expected_state, expected_actions in cases:
            with self.subTest(case=name):
                self.assertEqual(
                    signal_transition(
                        PositionState.FLAT, Decimal(z_score), Decimal(traditional),
                        Decimal(reverse), True, False,
                    ),
                    (expected_state, expected_actions),
                )

    # Mutation caught: making exit hysteresis exclusive or retaining an ineligible open position.
    def test_open_position_exit_boundaries_and_reversals(self):
        cases = (
            ("traditional_below_exit", PositionState.TRADITIONAL, "0.4999", "1", "1", PositionState.FLAT, ("exit_traditional",)),
            ("traditional_at_exit", PositionState.TRADITIONAL, "0.5", "1", "1", PositionState.FLAT, ("exit_traditional",)),
            ("traditional_above_exit", PositionState.TRADITIONAL, "0.5001", "1", "1", PositionState.TRADITIONAL, ()),
            ("reverse_below_exit", PositionState.REVERSE, "-0.4999", "1", "1", PositionState.FLAT, ("exit_reverse",)),
            ("reverse_at_exit", PositionState.REVERSE, "-0.5", "1", "1", PositionState.FLAT, ("exit_reverse",)),
            ("reverse_above_exit", PositionState.REVERSE, "-0.5001", "1", "1", PositionState.REVERSE, ()),
            ("traditional_zero_net", PositionState.TRADITIONAL, "1", "0", "1", PositionState.FLAT, ("exit_traditional",)),
            ("reverse_zero_net", PositionState.REVERSE, "-1", "1", "0", PositionState.FLAT, ("exit_reverse",)),
            ("reverse_to_traditional", PositionState.REVERSE, "2.0", "0.0001", "-1", PositionState.TRADITIONAL, ("exit_reverse", "enter_traditional")),
        )
        for name, prior, z_score, traditional, reverse, expected_state, expected_actions in cases:
            with self.subTest(case=name):
                self.assertEqual(
                    signal_transition(
                        prior, Decimal(z_score), Decimal(traditional), Decimal(reverse), True, False,
                    ),
                    (expected_state, expected_actions),
                )

    # Mutation caught: applying data checks before risk or creating actions while already flat.
    def test_risk_and_missing_data_precedence_preserve_flat_idempotence(self):
        cases = (
            ("risk_open", PositionState.TRADITIONAL, Decimal("2"), True, True, PositionState.FLAT, ("risk_flatten",)),
            ("risk_flat", PositionState.FLAT, None, False, True, PositionState.FLAT, ()),
            ("data_open", PositionState.REVERSE, None, False, False, PositionState.FLAT, ("data_flatten",)),
            ("data_flat", PositionState.FLAT, None, False, False, PositionState.FLAT, ()),
        )
        for name, prior, z_score, data_ready, risk_flatten, expected_state, expected_actions in cases:
            with self.subTest(case=name):
                self.assertEqual(
                    signal_transition(
                        prior, z_score, Decimal("1"), Decimal("1"), data_ready, risk_flatten,
                    ),
                    (expected_state, expected_actions),
                )

    # Mutation caught: allowing direct transition comparisons to alter caller Decimal context.
    def test_direct_transition_preserves_full_caller_decimal_context(self):
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
        expected_context = context_snapshot(constrained)
        try:
            setcontext(constrained.copy())
            self.assertEqual(
                signal_transition(
                    PositionState.FLAT, Decimal("2"), Decimal("0.0001"),
                    Decimal("-0.0001"), True, False,
                ),
                (PositionState.TRADITIONAL, ("enter_traditional",)),
            )
            self.assertEqual(context_snapshot(getcontext()), expected_context)
        finally:
            setcontext(original)

    # Mutation caught: accepting non-exact state, Decimal, or bool boundary inputs.
    def test_malformed_transition_inputs_return_none(self):
        cases = (
            (0, Decimal("1"), Decimal("1"), Decimal("1"), True, False),
            (PositionState.FLAT, 1.0, Decimal("1"), Decimal("1"), True, False),
            (PositionState.FLAT, Decimal("NaN"), Decimal("1"), Decimal("1"), True, False),
            (PositionState.FLAT, Decimal("Infinity"), Decimal("1"), Decimal("1"), True, False),
            (PositionState.FLAT, Decimal("1"), 1.0, Decimal("1"), True, False),
            (PositionState.FLAT, Decimal("1"), Decimal("NaN"), Decimal("1"), True, False),
            (PositionState.FLAT, Decimal("1"), Decimal("1"), Decimal("Infinity"), True, False),
            (PositionState.FLAT, Decimal("1"), Decimal("1"), Decimal("1"), 1, False),
            (PositionState.FLAT, Decimal("1"), Decimal("1"), Decimal("1"), True, 0),
        )
        for case in cases:
            with self.subTest(case=case):
                self.assertIsNone(signal_transition(*case))


class SignalDecisionTests(unittest.TestCase):
    @staticmethod
    def standard_prior():
        return history((Decimal("-5"),) * 123 + (Decimal("5"),) * 123 + (
            Decimal("-7.5"), Decimal("7.5"), Decimal("-2.5"),
            Decimal("2.5"), Decimal("0"), Decimal("0"),
        ))

    @staticmethod
    def current(gross, z_score, *, traditional="4", reverse="-2", count=252,
                quality=True, fresh=True):
        return replace(
            observation(
                "2Y", BASE_TIME + timedelta(minutes=252), Decimal(gross),
                z_score=Decimal(z_score) if z_score is not None else None,
                count=count, quality=quality, fresh=fresh,
            ),
            traditional_net_opportunity_bps=Decimal(traditional),
            reverse_net_opportunity_bps=Decimal(reverse),
        )

    def decision(self, observation_value, prior=None, prior_state=PositionState.FLAT,
                 risk_flatten=False, decision_id="decision-1", strategy="p32",
                 configuration="config-1"):
        return generate_signal_decision(
            decision_id,
            observation_value,
            self.standard_prior() if prior is None else prior,
            prior_state,
            risk_flatten,
            strategy,
            configuration,
        )

    # Mutation caught: omitting calculated features, changing field mapping, or returning mutable output.
    def test_traditional_entry_is_an_immutable_decision_with_literal_features(self):
        observation_value = self.current("10", "2", traditional="4.5", reverse="-1.5")
        decision = self.decision(observation_value)
        self.assertEqual(
            decision,
            SignalDecision(
                decision_id="decision-1",
                maturity="2Y",
                decision_time_utc=BASE_TIME + timedelta(minutes=252),
                prior_state=PositionState.FLAT,
                new_state=PositionState.TRADITIONAL,
                direction=TradeDirection.TRADITIONAL,
                reason_code="enter_traditional",
                feature_values=(
                    NamedValue("z_score", Decimal("2"), "standard_deviations"),
                    NamedValue("traditional_net_opportunity", Decimal("4.5"), "bps"),
                    NamedValue("reverse_net_opportunity", Decimal("-1.5"), "bps"),
                ),
                strategy_version="p32",
                configuration_version="config-1",
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            decision.reason_code = "changed"

    # Mutation caught: requiring a declared z-score even when causal history computes one.
    def test_nullable_declared_z_uses_computed_ready_decision(self):
        decision = self.decision(
            self.current("10", None, traditional="4.5", reverse="-1.5")
        )
        self.assertEqual(
            decision,
            SignalDecision(
                decision_id="decision-1",
                maturity="2Y",
                decision_time_utc=BASE_TIME + timedelta(minutes=252),
                prior_state=PositionState.FLAT,
                new_state=PositionState.TRADITIONAL,
                direction=TradeDirection.TRADITIONAL,
                reason_code="enter_traditional",
                feature_values=(
                    NamedValue("z_score", Decimal("2"), "standard_deviations"),
                    NamedValue("traditional_net_opportunity", Decimal("4.5"), "bps"),
                    NamedValue("reverse_net_opportunity", Decimal("-1.5"), "bps"),
                ),
                strategy_version="p32",
                configuration_version="config-1",
            ),
        )

    # Mutation caught: replacing no-action reason codes or losing supplied versions.
    def test_holds_and_flat_states_have_literal_reason_codes(self):
        cases = (
            ("flat", self.current("0", "0"), PositionState.FLAT, "remain_flat"),
            ("traditional", self.current("5", "1"), PositionState.TRADITIONAL, "hold_traditional"),
            ("reverse", self.current("-5", "-1", traditional="-2", reverse="4"), PositionState.REVERSE, "hold_reverse"),
        )
        for name, observation_value, prior_state, reason_code in cases:
            with self.subTest(case=name):
                decision = self.decision(
                    observation_value, prior_state=prior_state,
                    strategy="strategy-42", configuration="configuration-99",
                )
                self.assertEqual(decision.reason_code, reason_code)
                self.assertEqual(decision.strategy_version, "strategy-42")
                self.assertEqual(decision.configuration_version, "configuration-99")

    # Mutation caught: collapsing a reversal into one unordered action or incorrect reason joining.
    def test_reversal_joins_ordered_actions_in_its_reason_code(self):
        decision = self.decision(
            self.current("-10", "-2", traditional="-1", reverse="4"),
            prior_state=PositionState.TRADITIONAL,
        )
        self.assertEqual(decision.new_state, PositionState.REVERSE)
        self.assertEqual(decision.direction, TradeDirection.REVERSE)
        self.assertEqual(decision.reason_code, "exit_traditional_then_enter_reverse")

    # Mutation caught: allowing integrated causal arithmetic to leak caller Decimal context.
    def test_decision_generation_preserves_full_caller_decimal_context(self):
        observation_value = self.current("10", None, traditional="4.5", reverse="-1.5")
        prior = self.standard_prior()
        expected_decision = SignalDecision(
            decision_id="decision-1",
            maturity="2Y",
            decision_time_utc=BASE_TIME + timedelta(minutes=252),
            prior_state=PositionState.FLAT,
            new_state=PositionState.TRADITIONAL,
            direction=TradeDirection.TRADITIONAL,
            reason_code="enter_traditional",
            feature_values=(
                NamedValue("z_score", Decimal("2"), "standard_deviations"),
                NamedValue("traditional_net_opportunity", Decimal("4.5"), "bps"),
                NamedValue("reverse_net_opportunity", Decimal("-1.5"), "bps"),
            ),
            strategy_version="p32",
            configuration_version="config-1",
        )
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
        expected_context = context_snapshot(constrained)
        try:
            setcontext(constrained.copy())
            self.assertEqual(self.decision(observation_value, prior), expected_decision)
            self.assertEqual(context_snapshot(getcontext()), expected_context)
        finally:
            setcontext(original)

    # Mutation caught: treating unavailable but valid observations as malformed or changing unavailable features.
    def test_valid_unavailable_data_uses_flatten_or_data_unavailable_outcomes(self):
        stale = self.current("10", "2", fresh=False)
        poor_quality = self.current("10", "2", quality=False)
        mismatched_z = self.current("10", "3")
        short_prior = self.standard_prior()[:-1]
        zero_variance = history((Decimal("1"),) * 252)
        cases = (
            ("stale_open", stale, self.standard_prior(), PositionState.TRADITIONAL, PositionState.FLAT, "data_flatten", True),
            ("poor_quality_flat", poor_quality, self.standard_prior(), PositionState.FLAT, PositionState.FLAT, "data_unavailable", True),
            ("mismatched_z_flat", mismatched_z, self.standard_prior(), PositionState.FLAT, PositionState.FLAT, "data_unavailable", True),
            ("short_flat", self.current("10", "2"), short_prior, PositionState.FLAT, PositionState.FLAT, "data_unavailable", False),
            ("zero_variance_flat", self.current("10", "2"), zero_variance, PositionState.FLAT, PositionState.FLAT, "data_unavailable", False),
        )
        for name, observation_value, prior, prior_state, state, reason_code, has_z in cases:
            with self.subTest(case=name):
                decision = self.decision(observation_value, prior, prior_state)
                self.assertEqual(decision.new_state, state)
                self.assertEqual(decision.reason_code, reason_code)
                self.assertEqual(
                    tuple(value.name for value in decision.feature_values),
                    ("observation_count", "gross_excess_spread", "traditional_net_opportunity", "reverse_net_opportunity", "z_score")
                    if has_z else ("observation_count", "gross_excess_spread", "traditional_net_opportunity", "reverse_net_opportunity"),
                )

    # Mutation caught: changing unavailable feature values, units, or declaration order.
    def test_unavailable_features_are_complete_literal_named_values(self):
        stale = self.current("10", "2")
        stale = replace(stale, is_fresh=False)
        short = self.current("10", "2")
        cases = (
            (
                "calculated_z",
                stale,
                self.standard_prior(),
                (
                    NamedValue("observation_count", Decimal("252"), "observations"),
                    NamedValue("gross_excess_spread", Decimal("10"), "bps"),
                    NamedValue("traditional_net_opportunity", Decimal("4"), "bps"),
                    NamedValue("reverse_net_opportunity", Decimal("-2"), "bps"),
                    NamedValue("z_score", Decimal("2"), "standard_deviations"),
                ),
            ),
            (
                "missing_z",
                short,
                self.standard_prior()[:-1],
                (
                    NamedValue("observation_count", Decimal("252"), "observations"),
                    NamedValue("gross_excess_spread", Decimal("10"), "bps"),
                    NamedValue("traditional_net_opportunity", Decimal("4"), "bps"),
                    NamedValue("reverse_net_opportunity", Decimal("-2"), "bps"),
                ),
            ),
        )
        for name, observation_value, prior, expected_features in cases:
            with self.subTest(case=name):
                self.assertEqual(
                    self.decision(observation_value, prior).feature_values,
                    expected_features,
                )

    # Mutation caught: reading or validating history before an explicit risk flatten and emitting risk features.
    def test_risk_flatten_bypasses_malformed_history_and_economic_features(self):
        decision = self.decision(
            self.current("10", "2"), object(), PositionState.TRADITIONAL, True,
        )
        self.assertEqual(decision.new_state, PositionState.FLAT)
        self.assertEqual(decision.reason_code, "risk_flatten")
        self.assertEqual(decision.feature_values, ())
        already_flat = self.decision(self.current("10", "2"), object(), PositionState.FLAT, True)
        self.assertEqual(already_flat.reason_code, "risk_flatten_already_flat")

    # Mutation caught: accepting malformed boundary inputs instead of returning None.
    def test_malformed_decision_inputs_return_none(self):
        valid_observation = self.current("10", "2")
        cases = (
            (None, valid_observation, self.standard_prior(), PositionState.FLAT, False, "p32", "config"),
            ("", valid_observation, self.standard_prior(), PositionState.FLAT, False, "p32", "config"),
            ("id", object(), self.standard_prior(), PositionState.FLAT, False, "p32", "config"),
            ("id", valid_observation, object(), PositionState.FLAT, False, "p32", "config"),
            ("id", valid_observation, self.standard_prior(), 0, False, "p32", "config"),
            ("id", valid_observation, self.standard_prior(), PositionState.FLAT, 0, "p32", "config"),
            ("id", valid_observation, self.standard_prior(), PositionState.FLAT, False, "", "config"),
            ("id", valid_observation, self.standard_prior(), PositionState.FLAT, False, "p32", None),
        )
        for case in cases:
            with self.subTest(case=case):
                self.assertIsNone(generate_signal_decision(*case))

    # Mutation caught: treating corrupted exact-model history as valid but unavailable data.
    def test_corrupted_current_and_prior_are_malformed_not_data_decisions(self):
        valid_current = self.current("10", "2")
        cases = []
        for field, value in (
            ("gross_excess_spread_bps", Decimal("NaN")),
            ("gross_excess_spread_bps", 3),
            ("observation_time_utc", "not-a-datetime"),
        ):
            corrupted_current = replace(valid_current)
            object.__setattr__(corrupted_current, field, value)
            cases.append((f"current_{field}_{value!r}", corrupted_current, self.standard_prior()))

            corrupted_prior = self.standard_prior()
            object.__setattr__(corrupted_prior[-1], field, value)
            cases.append((f"prior_{field}_{value!r}", valid_current, corrupted_prior))

        for name, corrupted_current, corrupted_prior in cases:
            with self.subTest(case=name):
                try:
                    decision = self.decision(corrupted_current, corrupted_prior)
                except Exception as error:  # pragma: no cover - an exception is the failure detail
                    self.fail(f"generate_signal_decision raised {error!r}")
                self.assertIsNone(decision)


class OpportunityRankingTests(unittest.TestCase):
    @staticmethod
    def row(maturity, z_score, *, traditional="1", reverse="-1", count=252,
            quality=True, fresh=True, when=BASE_TIME):
        return replace(
            observation(
                maturity, when, Decimal("0"), z_score=Decimal(z_score) if z_score is not None else None,
                count=count, quality=quality, fresh=fresh,
            ),
            traditional_net_opportunity_bps=Decimal(traditional),
            reverse_net_opportunity_bps=Decimal(reverse),
        )

    # Mutation caught: sorting by signed z-score or ascending absolute z-score.
    def test_ranks_eligible_traditional_and_reverse_rows_by_descending_absolute_zscore(self):
        two_year = self.row("2Y", "2.1", traditional="1", reverse="-1")
        five_year = self.row("5Y", "-3", traditional="-1", reverse="1")
        self.assertEqual(rank_opportunities((two_year, five_year)), ("5Y", "2Y"))

    # Mutation caught: omitting the deterministic maturity-text tie-break.
    def test_equal_absolute_zscores_rank_maturity_text_ascending(self):
        five_year = self.row("5Y", "-3", traditional="-1", reverse="1")
        two_year = self.row("2Y", "3", traditional="1", reverse="-1")
        self.assertEqual(rank_opportunities((five_year, two_year)), ("2Y", "5Y"))

    # Mutation caught: treating a zero directional net as eligible.
    def test_nonpositive_directional_net_excludes_an_otherwise_extreme_row(self):
        blocked = self.row("2Y", "9", traditional="0", reverse="-1")
        eligible = self.row("5Y", "2", traditional="1", reverse="-1")
        self.assertEqual(rank_opportunities((blocked, eligible)), ("5Y",))

    # Mutation caught: allowing rows that lack every required current-data qualifier.
    def test_excludes_below_threshold_stale_poor_quality_missing_z_and_short_history_rows(self):
        eligible = self.row("7Y", "2", traditional="1", reverse="-1")
        rows = (
            self.row("1Y", "1.9999", traditional="1", reverse="-1"),
            self.row("2Y", "3", traditional="1", reverse="-1", fresh=False),
            self.row("3Y", "3", traditional="1", reverse="-1", quality=False),
            self.row("5Y", None, traditional="1", reverse="-1"),
            self.row("10Y", "3", traditional="1", reverse="-1", count=251),
            eligible,
        )
        self.assertEqual(rank_opportunities(rows), ("7Y",))

    # Mutation caught: returning None instead of the empty valid ranking.
    def test_no_eligible_rows_return_an_empty_tuple(self):
        self.assertEqual(rank_opportunities((self.row("2Y", "1.9"),)), ())

    # Mutation caught: rejecting a valid empty synchronized sequence.
    def test_empty_sequence_returns_an_empty_tuple(self):
        self.assertEqual(rank_opportunities(()), ())

    # Mutation caught: accepting unsynchronized, duplicated, or non-observation collections.
    def test_duplicate_maturity_mismatched_time_and_malformed_collections_return_none(self):
        two_year = self.row("2Y", "2")
        cases = (
            (two_year, self.row("2Y", "3", when=BASE_TIME)),
            (two_year, self.row("5Y", "3", when=BASE_TIME + timedelta(minutes=1))),
            object(),
            "not observations",
            (two_year, object()),
        )
        for value in cases:
            with self.subTest(value=value):
                self.assertIsNone(rank_opportunities(value))

    # Mutation caught: retaining caller input order when z-scores differ.
    def test_input_order_cannot_change_the_rank(self):
        two_year = self.row("2Y", "2.1", traditional="1", reverse="-1")
        five_year = self.row("5Y", "-3", traditional="-1", reverse="1")
        self.assertEqual(rank_opportunities((two_year, five_year)), ("5Y", "2Y"))
        self.assertEqual(rank_opportunities((five_year, two_year)), ("5Y", "2Y"))

    # Mutation caught: performing ranking arithmetic in the caller Decimal context.
    def test_preserves_a_nondefault_caller_decimal_context(self):
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
        rows = (
            self.row("2Y", "2.1", traditional="1", reverse="-1"),
            self.row("5Y", "-3", traditional="-1", reverse="1"),
        )
        try:
            setcontext(constrained.copy())
            self.assertEqual(rank_opportunities(rows), ("5Y", "2Y"))
            self.assertEqual(context_snapshot(getcontext()), expected)
        finally:
            setcontext(original)


if __name__ == "__main__":
    unittest.main()
