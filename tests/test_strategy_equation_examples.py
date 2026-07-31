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


def movement_direction(delta_bps: Decimal) -> int:
    if delta_bps >= D("5.00"):
        return 1
    if delta_bps <= D("-5.00"):
        return -1
    return 0


def transition(
    position: int,
    zscore: Decimal,
    traditional_net_bps: Decimal,
    reverse_net_bps: Decimal,
    data_ready: bool,
    risk_flatten: bool,
) -> tuple[int, tuple[str, ...]]:
    if risk_flatten:
        return (0, ("risk_flatten",)) if position else (0, ())
    if not data_ready:
        return (0, ("data_flatten",)) if position else (0, ())
    traditional_entry = zscore >= D("2.0") and traditional_net_bps > 0
    reverse_entry = zscore <= D("-2.0") and reverse_net_bps > 0
    if position == 0:
        if traditional_entry:
            return 1, ("enter_traditional",)
        if reverse_entry:
            return -1, ("enter_reverse",)
        return 0, ()
    if position == 1 and reverse_entry:
        return -1, ("exit_traditional", "enter_reverse")
    if position == -1 and traditional_entry:
        return 1, ("exit_reverse", "enter_traditional")
    if position == 1 and (abs(zscore) <= D("0.5") or traditional_net_bps <= 0):
        return 0, ("exit_traditional",)
    if position == -1 and (abs(zscore) <= D("0.5") or reverse_net_bps <= 0):
        return 0, ("exit_reverse",)
    return position, ()


def contract_pnl(
    quantity: int,
    multiplier_usd_per_point: Decimal,
    start_price: Decimal,
    end_price: Decimal,
    costs_usd: Decimal,
) -> Decimal:
    return D(quantity) * multiplier_usd_per_point * (end_price - start_price) - costs_usd


def round_half_away_positive(value: Decimal) -> int:
    return int(value.quantize(D("1"), rounding=ROUND_HALF_UP))


def select_hedge(
    direction: int,
    target_dv01: Decimal,
    swap_dv01: Decimal,
    treasury_dv01: Decimal,
) -> dict[str, object]:
    if direction not in (-1, 1) or target_dv01 <= 0 or swap_dv01 <= 0 or treasury_dv01 <= 0:
        return {
            "swap_quantity": 0,
            "treasury_quantity": 0,
            "net_dv01": D("0"),
            "residual_fraction": D("0"),
            "allowed": False,
        }
    swap_magnitude = round_half_away_positive(target_dv01 / swap_dv01)
    if swap_magnitude == 0:
        return {
            "swap_quantity": 0,
            "treasury_quantity": 0,
            "net_dv01": D("0"),
            "residual_fraction": D("0"),
            "allowed": False,
        }
    swap_quantity = direction * swap_magnitude
    delta_swap = -swap_dv01
    delta_treasury = -treasury_dv01
    continuous = -(D(swap_quantity) * delta_swap) / delta_treasury
    floor_quantity = int(continuous.to_integral_value(rounding=ROUND_FLOOR))
    candidates = (floor_quantity, floor_quantity + 1)

    def score(treasury_quantity: int) -> tuple[Decimal, Decimal, int]:
        net = D(swap_quantity) * delta_swap + D(treasury_quantity) * delta_treasury
        gross = abs(D(swap_quantity) * delta_swap) + abs(
            D(treasury_quantity) * delta_treasury
        )
        return abs(net), gross, treasury_quantity

    treasury_quantity = min(candidates, key=score)
    net_dv01 = D(swap_quantity) * delta_swap + D(treasury_quantity) * delta_treasury
    residual_fraction = abs(net_dv01) / target_dv01
    allowed = treasury_quantity != 0 and residual_fraction <= D("0.05")
    return {
        "swap_quantity": swap_quantity,
        "treasury_quantity": treasury_quantity,
        "net_dv01": net_dv01,
        "residual_fraction": residual_fraction,
        "allowed": allowed,
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


class MovementAndStateEquationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_movement_threshold_is_inclusive_in_both_directions(self) -> None:
        movement_fn = globals().get("movement_direction")
        self.assertTrue(callable(movement_fn), "movement_direction must be callable")
        for example in self.fixture["movement_boundaries"]:
            with self.subTest(delta_bps=example["delta_bps"]):
                self.assertEqual(
                    movement_fn(D(example["delta_bps"])),
                    example["expected_direction"],
                )

    def test_state_examples_preserve_action_order_and_flatten_precedence(self) -> None:
        transition_fn = globals().get("transition")
        self.assertTrue(callable(transition_fn), "transition must be callable")
        for example in self.fixture["state_examples"]:
            with self.subTest(example=example["id"]):
                actual = transition_fn(
                    example["position"],
                    D(example["zscore"]),
                    D(example["traditional_net_bps"]),
                    D(example["reverse_net_bps"]),
                    example["data_ready"],
                    example["risk_flatten"],
                )
                self.assertEqual(
                    actual,
                    (example["expected_position"], tuple(example["expected_actions"])),
                )

    def test_entry_thresholds_are_exact_and_inclusive(self) -> None:
        transition_fn = globals().get("transition")
        self.assertTrue(callable(transition_fn), "transition must be callable")
        traditional = [
            ("1.9999", (0, ())),
            ("2.0", (1, ("enter_traditional",))),
            ("2.0001", (1, ("enter_traditional",))),
        ]
        reverse = [
            ("-1.9999", (0, ())),
            ("-2.0", (-1, ("enter_reverse",))),
            ("-2.0001", (-1, ("enter_reverse",))),
        ]
        for zscore, expected in traditional:
            with self.subTest(direction="traditional", zscore=zscore):
                self.assertEqual(
                    transition_fn(0, D(zscore), D("1"), D("-1"), True, False),
                    expected,
                )
        for zscore, expected in reverse:
            with self.subTest(direction="reverse", zscore=zscore):
                self.assertEqual(
                    transition_fn(0, D(zscore), D("-1"), D("1"), True, False),
                    expected,
                )

    def test_exit_threshold_is_exact_and_inclusive(self) -> None:
        transition_fn = globals().get("transition")
        self.assertTrue(callable(transition_fn), "transition must be callable")
        cases = [
            ("0.4999", (0, ("exit_traditional",))),
            ("0.5", (0, ("exit_traditional",))),
            ("0.5001", (1, ())),
        ]
        for zscore, expected in cases:
            with self.subTest(zscore=zscore):
                self.assertEqual(
                    transition_fn(1, D(zscore), D("1"), D("-1"), True, False),
                    expected,
                )


class ContractPnlEquationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.examples = {item["id"]: item for item in cls.fixture["pnl_examples"]}

    def test_traditional_and_reverse_legs_use_signed_quantities(self) -> None:
        pnl_fn = globals().get("contract_pnl")
        self.assertTrue(callable(pnl_fn), "contract_pnl must be callable")
        expected_contracts = {
            "traditional_same_contract": [("YIT", 2, "1000"), ("ZT", -1, "2000")],
            "reverse_same_contract": [("YIW", -1, "1000"), ("ZF", 1, "1000")],
        }
        for example_id, expected_legs in expected_contracts.items():
            example = self.examples[example_id]
            with self.subTest(example=example_id):
                self.assertEqual(
                    [
                        (leg["symbol"], leg["quantity"], leg["multiplier_usd_per_point"])
                        for leg in example["legs"]
                    ],
                    expected_legs,
                )
                actual = sum(
                    (
                        pnl_fn(
                            leg["quantity"],
                            D(leg["multiplier_usd_per_point"]),
                            D(leg["start_price"]),
                            D(leg["end_price"]),
                            D(leg["costs_usd"]),
                        )
                        for leg in example["legs"]
                    ),
                    D("0"),
                )
                self.assertEqual(actual, D(example["expected_pnl_usd"]))

    def test_roll_marks_each_contract_only_over_its_own_price_history(self) -> None:
        pnl_fn = globals().get("contract_pnl")
        self.assertTrue(callable(pnl_fn), "contract_pnl must be callable")
        example = self.examples["eris_roll"]
        old = example["old_contract"]
        new = example["new_contract"]
        old_pnl = pnl_fn(
            old["quantity"],
            D(old["multiplier_usd_per_point"]),
            D(old["start_price"]),
            D(old["end_price"]),
            D("0"),
        )
        new_pnl = pnl_fn(
            new["quantity"],
            D(new["multiplier_usd_per_point"]),
            D(new["start_price"]),
            D(new["end_price"]),
            D("0"),
        )
        same_contract_pnl = old_pnl + new_pnl
        roll_cost = D(old["close_cost_usd"]) + D(new["open_cost_usd"])
        cross_contract_pnl = D(old["quantity"]) * D(
            old["multiplier_usd_per_point"]
        ) * (D(new["start_price"]) - D(old["end_price"]))
        self.assertEqual(same_contract_pnl, D(example["expected_same_contract_pnl_usd"]))
        self.assertNotEqual(same_contract_pnl, cross_contract_pnl)
        self.assertEqual(roll_cost, D(example["expected_roll_cost_usd"]))
        self.assertEqual(same_contract_pnl - roll_cost, D(example["expected_net_pnl_usd"]))
        self.assertEqual(
            abs(old["quantity"]) + abs(new["quantity"]),
            example["expected_contract_turnover"],
        )

    def test_reversal_charges_exit_and_entry_as_separate_actions(self) -> None:
        example = self.examples["reversal_cost"]
        total_cost = D(example["exit_cost_usd"]) + D(example["entry_cost_usd"])
        turnover = abs(example["exit_quantity"]) + abs(example["entry_quantity"])
        self.assertEqual(total_cost, D(example["expected_total_cost_usd"]))
        self.assertEqual(turnover, example["expected_contract_turnover"])


class IntegerDv01EquationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_positive_half_values_round_away_from_zero(self) -> None:
        round_fn = globals().get("round_half_away_positive")
        self.assertTrue(
            callable(round_fn), "round_half_away_positive must be callable"
        )
        cases = [("2.49", 2), ("2.5", 3), ("2.51", 3), ("0.5", 1)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(round_fn(D(value)), expected)

    def test_long_contract_rate_exposure_is_negative_dv01(self) -> None:
        self.assertEqual(-D("100"), D("-100"))
        self.assertEqual(-D("950"), D("-950"))

    def test_integer_hedges_match_exact_signed_fixture_results(self) -> None:
        select_fn = globals().get("select_hedge")
        self.assertTrue(callable(select_fn), "select_hedge must be callable")
        for example in self.fixture["hedge_examples"]:
            with self.subTest(example=example["id"]):
                actual = select_fn(
                    example["direction"],
                    D(example["target_dv01_usd_per_bp"]),
                    D(example["swap_dv01_usd_per_bp"]),
                    D(example["treasury_dv01_usd_per_bp"]),
                )
                self.assertEqual(
                    actual,
                    {
                        "swap_quantity": example["expected_swap_quantity"],
                        "treasury_quantity": example["expected_treasury_quantity"],
                        "net_dv01": D(example["expected_net_dv01_usd_per_bp"]),
                        "residual_fraction": D(example["expected_residual_fraction"]),
                        "allowed": example["expected_allowed"],
                    },
                )

    def test_invalid_inputs_return_blocked_zero_leg_result(self) -> None:
        select_fn = globals().get("select_hedge")
        self.assertTrue(callable(select_fn), "select_hedge must be callable")
        invalid_inputs = [
            (0, "1000", "100", "950"),
            (1, "0", "100", "950"),
            (1, "-1", "100", "950"),
            (1, "1000", "0", "950"),
            (1, "1000", "-1", "950"),
            (1, "1000", "100", "0"),
            (1, "1000", "100", "-1"),
        ]
        blocked = {
            "swap_quantity": 0,
            "treasury_quantity": 0,
            "net_dv01": D("0"),
            "residual_fraction": D("0"),
            "allowed": False,
        }
        for direction, target, swap, treasury in invalid_inputs:
            with self.subTest(
                direction=direction, target=target, swap=swap, treasury=treasury
            ):
                self.assertEqual(
                    select_fn(direction, D(target), D(swap), D(treasury)),
                    blocked,
                )
