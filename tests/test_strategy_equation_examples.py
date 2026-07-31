from __future__ import annotations

import json
import runpy
import unittest
from datetime import date, timedelta
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_FLOOR,
    ROUND_HALF_UP,
    getcontext,
    localcontext,
    setcontext,
)
from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "strategy_equation_examples.json"
_DECIMAL_CONTEXT_BEFORE_TESTS = None


def setUpModule() -> None:
    global _DECIMAL_CONTEXT_BEFORE_TESTS
    _DECIMAL_CONTEXT_BEFORE_TESTS = getcontext().copy()
    getcontext().prec = 50


def tearDownModule() -> None:
    if _DECIMAL_CONTEXT_BEFORE_TESTS is not None:
        setcontext(_DECIMAL_CONTEXT_BEFORE_TESTS)


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


def previous_weekday(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def select_causal_history(
    records: list[dict[str, str]],
    value_key: str,
    decision_date: str,
    decision_utc: str,
    limit: int,
) -> list[Decimal]:
    cutoff_date = date.fromisoformat(decision_date)
    by_date: dict[date, Decimal] = {}
    for record in records:
        observation_date = date.fromisoformat(record["observation_date"])
        if observation_date >= cutoff_date or record["available_utc"] > decision_utc:
            continue
        if observation_date in by_date:
            return []
        value = D(record[value_key])
        if not value.is_finite():
            return []
        by_date[observation_date] = value

    cursor = previous_weekday(cutoff_date)
    newest_first: list[Decimal] = []
    while len(newest_first) < limit and cursor in by_date:
        newest_first.append(by_date[cursor])
        cursor = previous_weekday(cursor)
    return list(reversed(newest_first))


def synchronized_decision_utc(records: list[dict[str, str]]) -> str | None:
    required = {"cms", "cmt", "floating", "repo"}
    if len(records) != 4 or {record["field"] for record in records} != required:
        return None
    dates = {record["observation_date"] for record in records}
    if len(dates) != 1:
        return None
    if any(record["available_utc"] <= record["observation_date"] for record in records):
        return None
    return max(record["available_utc"] for record in records)


def classify_economic_inputs(
    records: list[dict[str, object]], maturity: str, decision_utc: str
) -> str:
    required = {"cms", "cmt", "floating", "repo"}
    if len(records) != 4 or {str(record.get("field")) for record in records} != required:
        return "unavailable"
    classifications: set[str] = set()
    for record in records:
        if record.get("unit") != "bps" or record.get("maturity") != maturity:
            return "unavailable"
        if record.get("stale") is not False or str(record.get("available_utc")) > decision_utc:
            return "unavailable"
        try:
            value = D(record.get("value_bps"))
        except (InvalidOperation, ValueError):
            return "unavailable"
        if not value.is_finite():
            return "unavailable"
        classification = str(record.get("classification"))
        if classification not in {"exact", "proxy"}:
            return "unavailable"
        classifications.add(classification)
    return "proxy" if "proxy" in classifications else "exact"


def valid_positive_decimals(*values: object) -> bool:
    try:
        parsed = [D(value) for value in values]
    except (InvalidOperation, ValueError):
        return False
    return all(value.is_finite() and value > 0 for value in parsed)


def causal_zscore(current: Decimal, prior: list[Decimal]) -> Decimal | None:
    if len(prior) != 252:
        return None
    sigma = sample_std(prior)
    if sigma == 0:
        return None
    return (current - mean(prior)) / sigma


def synchronized_spread_movement(
    records: list[dict[str, object]],
    maturity: str,
    previous_observation_date: str,
    current_observation_date: str,
    previous_decision_utc: str,
    current_decision_utc: str,
) -> dict[str, Decimal | int] | None:
    previous_date = date.fromisoformat(previous_observation_date)
    current_date = date.fromisoformat(current_observation_date)
    if previous_weekday(current_date) != previous_date:
        return None

    spreads: list[Decimal] = []
    for observation_date, decision_utc in (
        (previous_observation_date, previous_decision_utc),
        (current_observation_date, current_decision_utc),
    ):
        snapshot = [
            record
            for record in records
            if record.get("observation_date") == observation_date
            and str(record.get("available_utc")) <= decision_utc
        ]
        synchronized_utc = synchronized_decision_utc(snapshot)
        if synchronized_utc is None or synchronized_utc > decision_utc:
            return None
        if classify_economic_inputs(snapshot, maturity, decision_utc) == "unavailable":
            return None
        values = {str(record["field"]): D(record["value_bps"]) for record in snapshot}
        spreads.append(values["cms"] - values["cmt"])

    delta = spreads[1] - spreads[0]
    return {"delta_spread_bps": delta, "direction": movement_direction(delta)}


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


def treasury_fractional_quote_to_points(
    whole_points: int, thirty_seconds: int, eighths_of_32nd: int
) -> Decimal | None:
    if (
        whole_points < 0
        or not 0 <= thirty_seconds < 32
        or not 0 <= eighths_of_32nd < 8
    ):
        return None
    return (
        D(whole_points)
        + D(thirty_seconds) / D("32")
        + D(eighths_of_32nd) / D("256")
    )


def tick_value_usd(
    minimum_increment_points: Decimal, multiplier_usd_per_point: Decimal
) -> Decimal:
    return minimum_increment_points * multiplier_usd_per_point


def causal_roll_pnl(
    example: dict[str, object], as_of_utc: str
) -> dict[str, Decimal | int] | None:
    old = example["old_contract"]
    new = example["new_contract"]
    boundary = str(example["roll_decision_utc"])
    if not (
        str(old["start_utc"])
        < str(old["end_utc"])
        == boundary
        == str(example["close_utc"])
        == str(example["open_utc"])
        == str(new["start_utc"])
        < str(new["end_utc"])
    ):
        return None
    if as_of_utc < boundary:
        return None

    old_pnl = contract_pnl(
        old["quantity"],
        D(old["multiplier_usd_per_point"]),
        D(old["start_price"]),
        D(old["end_price"]),
        D("0"),
    )
    new_pnl = D("0")
    if as_of_utc >= str(new["end_utc"]):
        new_pnl = contract_pnl(
            new["quantity"],
            D(new["multiplier_usd_per_point"]),
            D(new["start_price"]),
            D(new["end_price"]),
            D("0"),
        )
    roll_cost = D(old["close_cost_usd"]) + D(new["open_cost_usd"])
    return {
        "old_pnl_usd": old_pnl,
        "new_pnl_usd": new_pnl,
        "roll_cost_usd": roll_cost,
        "net_pnl_usd": old_pnl + new_pnl - roll_cost,
        "contract_turnover": abs(old["quantity"]) + abs(new["quantity"]),
    }


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

    def test_treasury_fractional_quotes_normalize_to_decimal_points(self) -> None:
        quote_fn = globals().get("treasury_fractional_quote_to_points")
        self.assertTrue(
            callable(quote_fn), "treasury_fractional_quote_to_points must be callable"
        )
        expected = {
            "traditional_same_contract": ("ZT", "102.000000", "101.984375"),
            "reverse_same_contract": ("ZF", "108.000000", "108.015625"),
        }
        for example_id, (root, start_price, end_price) in expected.items():
            leg = next(
                leg
                for leg in self.examples[example_id]["legs"]
                if str(leg["symbol"]).startswith(root)
            )
            with self.subTest(example=example_id, endpoint="start"):
                source = leg["start_source_quote"]
                self.assertEqual(
                    quote_fn(
                        source["whole_points"],
                        source["thirty_seconds"],
                        source["eighths_of_32nd"],
                    ),
                    D(start_price),
                )
            with self.subTest(example=example_id, endpoint="end"):
                source = leg["end_source_quote"]
                self.assertEqual(
                    quote_fn(
                        source["whole_points"],
                        source["thirty_seconds"],
                        source["eighths_of_32nd"],
                    ),
                    D(end_price),
                )

    def test_official_tick_grids_and_tick_values_are_exact(self) -> None:
        tick_fn = globals().get("tick_value_usd")
        self.assertTrue(callable(tick_fn), "tick_value_usd must be callable")
        conventions = self.fixture["quote_conventions"]
        for root, convention in conventions.items():
            with self.subTest(root=root):
                tick = D(convention["minimum_increment_points"])
                multiplier = D(convention["multiplier_usd_per_point"])
                self.assertEqual(
                    tick_fn(tick, multiplier),
                    D(convention["tick_value_usd"]),
                )
                for example in self.examples.values():
                    for leg in example.get("legs", []):
                        if str(leg["symbol"]).startswith(root):
                            for endpoint in ("start_price", "end_price"):
                                ticks = D(leg[endpoint]) / tick
                                self.assertEqual(ticks, ticks.to_integral_value())

    def test_same_contract_examples_use_full_immutable_expiry_ids(self) -> None:
        for example_id in ("traditional_same_contract", "reverse_same_contract"):
            for leg in self.examples[example_id]["legs"]:
                with self.subTest(example=example_id, instrument=leg["symbol"]):
                    self.assertRegex(
                        leg["symbol"], r"^(?:YIT|YIW|ZT|ZF)[FGHJKMNQUVXZ]\d{2}$"
                    )
                    self.assertIsNotNone(leg.get("start_instrument_id"))
                    self.assertIsNotNone(leg.get("end_instrument_id"))
                    self.assertEqual(leg.get("start_instrument_id"), leg["symbol"])
                    self.assertEqual(leg.get("end_instrument_id"), leg["symbol"])

    def test_traditional_and_reverse_legs_use_signed_quantities(self) -> None:
        pnl_fn = globals().get("contract_pnl")
        self.assertTrue(callable(pnl_fn), "contract_pnl must be callable")
        expected_contracts = {
            "traditional_same_contract": [
                ("YITH27", 2, "1000"),
                ("ZTH27", -1, "2000"),
            ],
            "reverse_same_contract": [
                ("YIWH27", -1, "1000"),
                ("ZFH27", 1, "1000"),
            ],
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

    def test_roll_clock_separates_old_and_new_holding_intervals(self) -> None:
        roll_fn = globals().get("causal_roll_pnl")
        self.assertTrue(callable(roll_fn), "causal_roll_pnl must be callable")
        example = self.examples["eris_roll"]
        at_roll = roll_fn(example, example["roll_decision_utc"])
        self.assertEqual(
            at_roll,
            {
                "old_pnl_usd": D(example["expected_old_pnl_at_roll_usd"]),
                "new_pnl_usd": D("0"),
                "roll_cost_usd": D(example["expected_roll_cost_usd"]),
                "net_pnl_usd": D(example["expected_net_at_roll_usd"]),
                "contract_turnover": example["expected_contract_turnover"],
            },
        )
        after_new_interval = roll_fn(example, example["new_contract"]["end_utc"])
        self.assertEqual(
            after_new_interval,
            {
                "old_pnl_usd": D(example["expected_old_pnl_at_roll_usd"]),
                "new_pnl_usd": D("10"),
                "roll_cost_usd": D(example["expected_roll_cost_usd"]),
                "net_pnl_usd": D(example["expected_net_pnl_usd"]),
                "contract_turnover": example["expected_contract_turnover"],
            },
        )

        future_price = json.loads(json.dumps(example))
        future_price["new_contract"]["end_price"] = "999"
        self.assertEqual(roll_fn(future_price, example["roll_decision_utc"]), at_roll)

        for shifted_start in (
            "2027-03-10T20:30:59Z",
            "2027-03-10T20:31:01Z",
        ):
            with self.subTest(new_start_utc=shifted_start):
                invalid = json.loads(json.dumps(example))
                invalid["new_contract"]["start_utc"] = shifted_start
                self.assertIsNone(roll_fn(invalid, example["new_contract"]["end_utc"]))

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


class TimingAndClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_previous_weekday_uses_the_declared_monday_friday_calendar(self) -> None:
        previous_fn = globals().get("previous_weekday")
        self.assertTrue(callable(previous_fn), "previous_weekday must be callable")
        self.assertEqual(previous_fn(date(2027, 3, 1)), date(2027, 2, 26))

    def test_consecutive_lagged_funding_is_causal_and_breaks_at_a_gap(self) -> None:
        consecutive_fn = globals().get("select_causal_history")
        self.assertTrue(
            callable(consecutive_fn), "select_causal_history must be callable"
        )
        decision = "2027-03-01"
        decision_utc = "2027-03-01T20:31:00Z"
        cursor = date.fromisoformat(decision)
        dates_newest_first: list[date] = []
        for _ in range(61):
            cursor = previous_weekday(cursor)
            dates_newest_first.append(cursor)
        records = [
            {
                "observation_date": observation_date.isoformat(),
                "available_utc": f"{observation_date.isoformat()}T20:00:00Z",
                "funding_spread_bps": "5",
            }
            for observation_date in reversed(dates_newest_first)
        ]

        selected = consecutive_fn(
            records, "funding_spread_bps", decision, decision_utc, 60
        )
        self.assertEqual(len(selected), 60)
        self.assertEqual(funding_expectation(selected), D("5"))
        self.assertEqual(records[-1]["observation_date"], "2027-02-26")

        forty_records = records[:1] + records[21:]
        forty_selected = consecutive_fn(
            forty_records, "funding_spread_bps", decision, decision_utc, 60
        )
        self.assertEqual(len(forty_selected), 40)
        self.assertEqual(funding_expectation(forty_selected), D("5"))

        second_newest_date = records[-2]["observation_date"]
        broken_records = [
            record
            for record in records
            if record["observation_date"] != second_newest_date
        ]
        broken_selected = consecutive_fn(
            broken_records, "funding_spread_bps", decision, decision_utc, 60
        )
        self.assertEqual(broken_selected, [D("5")])
        self.assertIsNone(funding_expectation(broken_selected))

        bounded_records = records + [
            {
                "observation_date": decision,
                "available_utc": decision_utc,
                "funding_spread_bps": "999",
            },
            {
                "observation_date": "2027-03-02",
                "available_utc": "2027-03-02T20:31:00Z",
                "funding_spread_bps": "999",
            },
        ]
        self.assertEqual(
            consecutive_fn(
                bounded_records, "funding_spread_bps", decision, decision_utc, 60
            ),
            selected,
        )

        revised_old_record = [dict(record) for record in records]
        revised_old_record[0]["funding_spread_bps"] = "999"
        self.assertEqual(
            consecutive_fn(
                revised_old_record,
                "funding_spread_bps",
                decision,
                decision_utc,
                60,
            ),
            selected,
        )

        eligible_duplicate = records + [dict(records[-1])]
        self.assertEqual(
            consecutive_fn(
                eligible_duplicate,
                "funding_spread_bps",
                decision,
                decision_utc,
                60,
            ),
            [],
        )
        late_latest = [dict(record) for record in records]
        late_latest[-1]["available_utc"] = "2027-03-01T20:31:01Z"
        self.assertEqual(
            consecutive_fn(
                late_latest, "funding_spread_bps", decision, decision_utc, 60
            ),
            [],
        )
        late_revision = dict(records[-1])
        late_revision["available_utc"] = "2027-03-01T20:31:01Z"
        late_revision["funding_spread_bps"] = "999"
        self.assertEqual(
            consecutive_fn(
                records + [late_revision],
                "funding_spread_bps",
                decision,
                decision_utc,
                60,
            ),
            selected,
        )

    def test_dated_zscore_history_enforces_publication_cutoff_and_exact_dates(
        self,
    ) -> None:
        select_fn = globals().get("select_causal_history")
        self.assertTrue(callable(select_fn), "select_causal_history must be callable")
        decision = "2027-03-01"
        decision_utc = "2027-03-01T20:31:00Z"
        cursor = date.fromisoformat(decision)
        newest_first: list[date] = []
        for _ in range(252):
            cursor = previous_weekday(cursor)
            newest_first.append(cursor)
        profile = expand_segments(
            self.fixture["gross_history_profiles"]["mean_0_sd_5_252"]
        )
        records = [
            {
                "observation_date": observation_date.isoformat(),
                "available_utc": f"{observation_date.isoformat()}T20:00:00Z",
                "gross_bps": str(value),
            }
            for observation_date, value in zip(reversed(newest_first), profile)
        ]
        selected = select_fn(records, "gross_bps", decision, decision_utc, 252)
        self.assertEqual(len(selected), 252)
        self.assertEqual(causal_zscore(D("10"), selected), D("2"))

        missing = records[:-2] + records[-1:]
        missing_selected = select_fn(
            missing, "gross_bps", decision, decision_utc, 252
        )
        self.assertEqual(len(missing_selected), 1)
        self.assertIsNone(causal_zscore(D("10"), missing_selected))

        duplicate = records + [dict(records[-1])]
        self.assertEqual(
            select_fn(duplicate, "gross_bps", decision, decision_utc, 252), []
        )

        late_latest = [dict(record) for record in records]
        late_latest[-1]["available_utc"] = "2027-03-01T20:31:01Z"
        self.assertEqual(
            select_fn(late_latest, "gross_bps", decision, decision_utc, 252), []
        )

        later_rows = [
            {
                "observation_date": decision,
                "available_utc": decision_utc,
                "gross_bps": "999",
            },
            {
                "observation_date": "2027-03-02",
                "available_utc": "2027-03-02T20:31:00Z",
                "gross_bps": "999",
            },
            {
                **records[-1],
                "available_utc": "2027-03-01T20:31:01Z",
                "gross_bps": "999",
            },
        ]
        self.assertEqual(
            select_fn(
                records + later_rows, "gross_bps", decision, decision_utc, 252
            ),
            selected,
        )

    def test_spread_movement_uses_adjacent_synchronized_saved_decisions(
        self,
    ) -> None:
        movement_fn = globals().get("synchronized_spread_movement")
        self.assertTrue(
            callable(movement_fn), "synchronized_spread_movement must be callable"
        )
        previous_date = "2027-01-05"
        current_date = "2027-01-06"
        previous_decision = "2027-01-05T20:31:00Z"
        current_decision = "2027-01-06T20:31:00Z"

        def snapshot(observation_date: str, cms: str, cmt: str) -> list[dict[str, object]]:
            values = {"cms": cms, "cmt": cmt, "floating": "5", "repo": "1"}
            return [
                {
                    "field": field,
                    "observation_date": observation_date,
                    "available_utc": f"{observation_date}T20:{28 + index:02d}:00Z",
                    "maturity": "2Y",
                    "unit": "bps",
                    "stale": False,
                    "classification": "exact",
                    "value_bps": value,
                }
                for index, (field, value) in enumerate(values.items())
            ]

        records = snapshot(previous_date, "450", "420") + snapshot(
            current_date, "455", "420"
        )
        expected = {"delta_spread_bps": D("5"), "direction": 1}
        actual = movement_fn(
            records,
            "2Y",
            previous_date,
            current_date,
            previous_decision,
            current_decision,
        )
        self.assertEqual(actual, expected)

        missing_endpoint = [
            record
            for record in records
            if not (
                record["observation_date"] == current_date
                and record["field"] == "cmt"
            )
        ]
        self.assertIsNone(
            movement_fn(
                missing_endpoint,
                "2Y",
                previous_date,
                current_date,
                previous_decision,
                current_decision,
            )
        )
        self.assertIsNone(
            movement_fn(
                records,
                "2Y",
                previous_date,
                "2027-01-07",
                previous_decision,
                current_decision,
            )
        )

        late_current = [dict(record) for record in records]
        late_current[-1]["available_utc"] = "2027-01-06T20:31:01Z"
        self.assertIsNone(
            movement_fn(
                late_current,
                "2Y",
                previous_date,
                current_date,
                previous_decision,
                current_decision,
            )
        )

        later = snapshot("2027-01-07", "999", "0")
        self.assertEqual(
            movement_fn(
                records + later,
                "2Y",
                previous_date,
                current_date,
                previous_decision,
                current_decision,
            ),
            expected,
        )

        eligible_duplicate = records + [dict(records[-1])]
        self.assertIsNone(
            movement_fn(
                eligible_duplicate,
                "2Y",
                previous_date,
                current_date,
                previous_decision,
                current_decision,
            )
        )
        late_revision = dict(records[-1])
        late_revision["available_utc"] = "2027-01-06T20:31:01Z"
        late_revision["value_bps"] = "999"
        self.assertEqual(
            movement_fn(
                records + [late_revision],
                "2Y",
                previous_date,
                current_date,
                previous_decision,
                current_decision,
            ),
            expected,
        )

    def test_synchronized_decision_requires_exact_field_and_date_identity(self) -> None:
        synchronized_fn = globals().get("synchronized_decision_utc")
        self.assertTrue(
            callable(synchronized_fn), "synchronized_decision_utc must be callable"
        )
        records = [
            {
                "field": "cms",
                "observation_date": "2027-01-06",
                "available_utc": "2027-01-06T20:29:00Z",
            },
            {
                "field": "cmt",
                "observation_date": "2027-01-06",
                "available_utc": "2027-01-06T20:30:00Z",
            },
            {
                "field": "floating",
                "observation_date": "2027-01-06",
                "available_utc": "2027-01-06T20:28:00Z",
            },
            {
                "field": "repo",
                "observation_date": "2027-01-06",
                "available_utc": "2027-01-06T20:31:00Z",
            },
        ]
        saved_decision = synchronized_fn(records)
        self.assertEqual(saved_decision, "2027-01-06T20:31:00Z")
        self.assertIsNone(synchronized_fn(records[:-1]))

        prior_date = [dict(record) for record in records]
        prior_date[3]["observation_date"] = "2027-01-05"
        self.assertIsNone(synchronized_fn(prior_date))

        later = [dict(record) for record in records]
        later[0]["available_utc"] = "2027-01-06T20:32:00Z"
        self.assertEqual(synchronized_fn(later), "2027-01-06T20:32:00Z")

        premature = [dict(record) for record in records]
        premature[0]["available_utc"] = "2027-01-06"
        self.assertIsNone(synchronized_fn(premature))

        future_records = [
            {
                "field": record["field"],
                "observation_date": "2027-01-07",
                "available_utc": "2027-01-07T20:31:00Z",
            }
            for record in records
        ]
        archive = records + future_records
        self.assertEqual(len(archive), 8)
        self.assertEqual(saved_decision, "2027-01-06T20:31:00Z")
        self.assertEqual(synchronized_fn(archive[:4]), saved_decision)

    def test_economic_input_classification_is_mutually_exclusive_and_fail_closed(
        self,
    ) -> None:
        classify_fn = globals().get("classify_economic_inputs")
        self.assertTrue(
            callable(classify_fn), "classify_economic_inputs must be callable"
        )
        decision_utc = "2027-01-06T20:31:00Z"
        records: list[dict[str, object]] = [
            {
                "field": field,
                "unit": "bps",
                "maturity": "2Y",
                "stale": False,
                "available_utc": available_utc,
                "value_bps": value_bps,
                "classification": "exact",
            }
            for field, available_utc, value_bps in [
                ("cms", "2027-01-06T20:29:00Z", "420"),
                ("cmt", "2027-01-06T20:30:00Z", "400"),
                ("floating", "2027-01-06T20:28:00Z", "4.5"),
                ("repo", "2027-01-06T20:31:00Z", "4.0"),
            ]
        ]
        saved_result = classify_fn(records, "2Y", decision_utc)
        self.assertEqual(saved_result, "exact")

        proxy = [dict(record) for record in records]
        proxy[3]["classification"] = "proxy"
        self.assertEqual(classify_fn(proxy, "2Y", decision_utc), "proxy")

        duplicate_cms = [dict(record) for record in records]
        duplicate_cms[3]["field"] = "cms"
        malformed_cases = {
            "missing_repo": records[:-1],
            "duplicate_cms": duplicate_cms,
        }
        field_mutations = {
            "wrong_unit": ("unit", "decimal"),
            "wrong_maturity": ("maturity", "5Y"),
            "stale": ("stale", True),
            "late": ("available_utc", "2027-01-06T20:31:01Z"),
            "nonfinite": ("value_bps", "NaN"),
            "unrecognized_classification": ("classification", "assumed"),
        }
        for name, (field, value) in field_mutations.items():
            mutated = [dict(record) for record in records]
            mutated[0][field] = value
            malformed_cases[name] = mutated
        missing_value = [dict(record) for record in records]
        del missing_value[0]["value_bps"]
        malformed_cases["missing_value"] = missing_value

        for name, malformed in malformed_cases.items():
            with self.subTest(case=name):
                self.assertEqual(
                    classify_fn(malformed, "2Y", decision_utc), "unavailable"
                )

        future_replacement = [dict(record) for record in records]
        future_replacement[3]["available_utc"] = "2027-01-07T20:31:00Z"
        self.assertEqual(
            classify_fn(future_replacement, "2Y", decision_utc), "unavailable"
        )
        self.assertEqual(saved_result, "exact")

    def test_contract_inputs_must_be_positive_and_two_leg_baskets_fail_closed(
        self,
    ) -> None:
        positive_fn = globals().get("valid_positive_decimals")
        self.assertTrue(
            callable(positive_fn), "valid_positive_decimals must be callable"
        )
        self.assertTrue(positive_fn("1000", "100.25", "100", "950"))
        invalid_values = [None, "0", "-1", "NaN", "Infinity", "-Infinity"]
        input_names = ["multiplier", "price", "swap_dv01", "treasury_dv01"]
        valid = ["1000", "100.25", "100", "950"]
        for input_index, input_name in enumerate(input_names):
            for invalid in invalid_values:
                with self.subTest(input=input_name, value=invalid):
                    candidate = list(valid)
                    candidate[input_index] = invalid
                    self.assertFalse(positive_fn(*candidate))

        select_fn = globals().get("select_hedge")
        self.assertTrue(callable(select_fn), "select_hedge must be callable")
        self.assertEqual(
            select_fn(1, D("1000"), D("100"), D("0")),
            {
                "swap_quantity": 0,
                "treasury_quantity": 0,
                "net_dv01": D("0"),
                "residual_fraction": D("0"),
                "allowed": False,
            },
        )


class TestModuleIsolationTests(unittest.TestCase):
    def test_loading_test_module_does_not_mutate_decimal_context(self) -> None:
        with localcontext() as context:
            context.prec = 28
            precision_before = getcontext().prec
            runpy.run_path(str(Path(__file__)))
            self.assertEqual(getcontext().prec, precision_before)
