from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from strategy import (
    ContractMetadata,
    FlattenUrgency,
    InstrumentObservation,
    MarketSnapshot,
    NamedValue,
    OrderSide,
    OrderType,
    PaperPosition,
    PositionState,
    RateObservation,
    RiskDecision,
    SignalDecision,
    SpreadObservation,
    TargetPosition,
    TimeInForce,
    TradeDirection,
    WorkingOrder,
    OrderIntent,
    to_csv_row,
)


UTC = timezone.utc
OBSERVED_AT = datetime(2026, 8, 3, 20, tzinfo=UTC)
AVAILABLE_AT = datetime(2026, 8, 3, 20, 30, tzinfo=UTC)
NOW = datetime(2026, 8, 3, 21, tzinfo=UTC)


class StrategyModelTests(unittest.TestCase):
    def test_to_csv_row_serializes_signal_decision_as_a_stable_literal_row(self) -> None:
        decision = SignalDecision(
            decision_id="decision-1", maturity="2Y", decision_time_utc=NOW,
            prior_state=PositionState.FLAT, new_state=PositionState.TRADITIONAL,
            direction=TradeDirection.TRADITIONAL, reason_code="entry_threshold",
            feature_values=(NamedValue("z_score", Decimal("1.75"), "standard_deviations"),),
            strategy_version="swap-arb-1", configuration_version="config-1",
        )

        self.assertEqual(
            to_csv_row(decision),
            {
                "decision_id": "decision-1",
                "maturity": "2Y",
                "decision_time_utc": "2026-08-03T21:00:00Z",
                "prior_state": "0",
                "new_state": "1",
                "direction": "1",
                "reason_code": "entry_threshold",
                "feature_values": '[{"name":"z_score","value":"1.75",'
                                  '"unit":"standard_deviations"}]',
                "strategy_version": "swap-arb-1",
                "configuration_version": "config-1",
            },
        )

    def test_to_csv_row_serializes_scalars_nested_values_and_preserves_records(self) -> None:
        spread = SpreadObservation(
            maturity="2Y", observation_time_utc=NOW,
            fixed_swap_spread_bps=Decimal("12.5"),
            expected_funding_spread_bps=Decimal("3"),
            gross_excess_spread_bps=Decimal("9.5"),
            traditional_cost_buffer_bps=Decimal("1.2"),
            reverse_cost_buffer_bps=Decimal("1.4"),
            traditional_net_opportunity_bps=Decimal("8.3"),
            reverse_net_opportunity_bps=Decimal("-10.9"),
            z_score=None, observation_count=60, source_quality_ok=True, is_fresh=False,
        )
        risk = RiskDecision(
            allowed=True, scale=Decimal("1E+3") / Decimal("1E+3"), reason_codes=("within_limits",),
            flatten_requested=False, urgency=FlattenUrgency.SCHEDULED,
            limits=(NamedValue("amount", Decimal("1E+3"), "usd"),), measured_values=(),
        )

        spread_row = to_csv_row(spread)
        risk_row = to_csv_row(risk)

        self.assertEqual(spread_row["z_score"], "")
        self.assertEqual(risk_row["allowed"], "true")
        self.assertEqual(risk_row["flatten_requested"], "false")
        self.assertEqual(risk_row["urgency"], "scheduled")
        self.assertEqual(risk_row["limits"], '[{"name":"amount","value":"1000","unit":"usd"}]')
        self.assertEqual(tuple(risk_row), tuple(field.name for field in fields(RiskDecision)))
        self.assertIsNot(risk_row, to_csv_row(risk))
        self.assertEqual(risk.limits[0].value, Decimal("1E+3"))

    def test_to_csv_row_rejects_non_dataclass_values_and_dataclass_classes(self) -> None:
        for value in (1, object(), NamedValue):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    to_csv_row(value)

    def test_spread_observation_is_frozen_and_accepts_signed_opportunities(self) -> None:
        observation = SpreadObservation(
            maturity="2Y", observation_time_utc=NOW,
            fixed_swap_spread_bps=Decimal("12.5"),
            expected_funding_spread_bps=Decimal("3.0"),
            gross_excess_spread_bps=Decimal("9.5"),
            traditional_cost_buffer_bps=Decimal("1.2"),
            reverse_cost_buffer_bps=Decimal("1.4"),
            traditional_net_opportunity_bps=Decimal("8.3"),
            reverse_net_opportunity_bps=Decimal("-10.9"),
            z_score=Decimal("1.75"), observation_count=60,
            source_quality_ok=True, is_fresh=True,
        )

        self.assertEqual(observation.reverse_net_opportunity_bps, Decimal("-10.9"))
        with self.assertRaises(AttributeError):
            observation.maturity = "5Y"

    def test_signal_decision_normalizes_features_and_is_frozen(self) -> None:
        decision = SignalDecision(
            decision_id="decision-1", maturity="2Y", decision_time_utc=NOW,
            prior_state=PositionState.FLAT, new_state=PositionState.TRADITIONAL,
            direction=TradeDirection.TRADITIONAL, reason_code="entry_threshold",
            feature_values=[NamedValue("z_score", Decimal("1.75"), "standard_deviations")],
            strategy_version="swap-arb-1", configuration_version="config-1",
        )

        self.assertIsInstance(decision.feature_values, tuple)
        with self.assertRaises(AttributeError):
            decision.reason_code = "other"

    def test_target_position_allows_signed_quantities_and_is_frozen(self) -> None:
        target = TargetPosition(
            maturity="2Y", swap_instrument_id="ERIS-YIT",
            treasury_instrument_id="CME-ZT", swap_quantity_contracts=-3,
            treasury_quantity_contracts=7, target_dv01_usd_per_bp=Decimal("1000"),
            gross_dv01_usd_per_bp=Decimal("1990"),
            residual_net_dv01_usd_per_bp=Decimal("-10"),
            expected_turnover_contracts=10, expected_cost_usd=Decimal("25.5"),
            rounding_diagnostic="minimum_residual", cap_diagnostic="within_caps",
        )

        self.assertEqual(target.residual_net_dv01_usd_per_bp, Decimal("-10"))
        with self.assertRaises(AttributeError):
            target.expected_cost_usd = Decimal("0")

    def test_risk_decision_normalizes_collections_and_is_frozen(self) -> None:
        decision = RiskDecision(
            allowed=True, scale=Decimal("0.75"), reason_codes=["within_limits"],
            flatten_requested=False, urgency=FlattenUrgency.NONE,
            limits=[NamedValue("gross_dv01", Decimal("5000"), "usd_per_bp")],
            measured_values=[NamedValue("gross_dv01", Decimal("1990"), "usd_per_bp")],
        )

        self.assertEqual(decision.reason_codes, ("within_limits",))
        self.assertIsInstance(decision.limits, tuple)
        with self.assertRaises(AttributeError):
            decision.scale = Decimal("1")

    def test_order_intent_accepts_ordered_schedule_and_is_frozen(self) -> None:
        intent = OrderIntent(
            run_id="run-1", agent_id="agent-0", strategy_id="swap-arb",
            decision_id="decision-1", instrument_id="ERIS-YIT", side=OrderSide.SELL,
            quantity_contracts=3, order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY, earliest_submission_utc=NOW,
            activate_at_utc=NOW + timedelta(minutes=1),
            expires_at_utc=NOW + timedelta(hours=1),
            reference_price_points=Decimal("99.25"),
            max_slippage_price_points=Decimal("0.05"), paper_only=True,
        )

        self.assertTrue(intent.paper_only)
        with self.assertRaises(AttributeError):
            intent.quantity_contracts = 0

    def test_output_records_reject_invalid_values(self) -> None:
        feature = NamedValue("z_score", Decimal("1.75"), "standard_deviations")
        limit = NamedValue("gross_dv01", Decimal("5000"), "usd_per_bp")
        cases = (
            (SpreadObservation, ("", NOW, Decimal("12.5"), Decimal("3"), Decimal("9.5"), Decimal("1.2"), Decimal("1.4"), Decimal("8.3"), Decimal("-10.9"), None, 60, True, True)),
            (SpreadObservation, ("2Y", datetime(2026, 8, 3, 21), Decimal("12.5"), Decimal("3"), Decimal("9.5"), Decimal("1.2"), Decimal("1.4"), Decimal("8.3"), Decimal("-10.9"), None, 60, True, True)),
            (SpreadObservation, ("2Y", datetime(2026, 8, 3, 21, tzinfo=timezone(timedelta(hours=1))), Decimal("12.5"), Decimal("3"), Decimal("9.5"), Decimal("1.2"), Decimal("1.4"), Decimal("8.3"), Decimal("-10.9"), None, 60, True, True)),
            (SpreadObservation, ("2Y", NOW, Decimal("NaN"), Decimal("3"), Decimal("9.5"), Decimal("1.2"), Decimal("1.4"), Decimal("8.3"), Decimal("-10.9"), None, 60, True, True)),
            (SpreadObservation, ("2Y", NOW, Decimal("12.5"), Decimal("3"), Decimal("9.5"), Decimal("-1"), Decimal("1.4"), Decimal("8.3"), Decimal("-10.9"), None, 60, True, True)),
            (SpreadObservation, ("2Y", NOW, Decimal("12.5"), Decimal("3"), Decimal("9.5"), Decimal("1.2"), Decimal("1.4"), Decimal("8.3"), Decimal("-10.9"), None, -1, True, True)),
            (SpreadObservation, ("2Y", NOW, Decimal("12.5"), Decimal("3"), Decimal("9.5"), Decimal("1.2"), Decimal("1.4"), Decimal("8.3"), Decimal("-10.9"), None, 60, 1, True)),
            (SignalDecision, ("decision", "2Y", NOW, 0, PositionState.FLAT, TradeDirection.FLAT, "reason", (feature,), "v", "c")),
            (SignalDecision, ("decision", "2Y", NOW, PositionState.FLAT, PositionState.FLAT, "FLAT", "reason", (feature,), "v", "c")),
            (SignalDecision, ("decision", "2Y", NOW, PositionState.FLAT, PositionState.FLAT, TradeDirection.FLAT, "reason", ("not-a-feature",), "v", "c")),
            (TargetPosition, ("2Y", "swap", "treasury", 1, 1, Decimal("-1"), Decimal("1"), Decimal("0"), 0, Decimal("0"), "round", "cap")),
            (TargetPosition, ("2Y", "swap", "treasury", 1, 1, Decimal("1"), Decimal("-1"), Decimal("0"), 0, Decimal("0"), "round", "cap")),
            (TargetPosition, ("2Y", "swap", "treasury", 1, 1, Decimal("1"), Decimal("1"), Decimal("0"), -1, Decimal("0"), "round", "cap")),
            (TargetPosition, ("2Y", "swap", "treasury", 1, 1, Decimal("1"), Decimal("1"), Decimal("0"), 0, Decimal("-1"), "round", "cap")),
            (RiskDecision, (True, Decimal("-0.01"), ("within_limits",), False, FlattenUrgency.NONE, (limit,), (limit,))),
            (RiskDecision, (True, Decimal("1.01"), ("within_limits",), False, FlattenUrgency.NONE, (limit,), (limit,))),
            (RiskDecision, (True, Decimal("0.75"), ("duplicate", "duplicate"), False, FlattenUrgency.NONE, (limit,), (limit,))),
            (RiskDecision, (True, Decimal("0.75"), ("",), False, FlattenUrgency.NONE, (limit,), (limit,))),
            (RiskDecision, (True, Decimal("0.75"), ("ok",), False, "none", (limit,), (limit,))),
            (RiskDecision, (True, Decimal("0.75"), ("ok",), False, FlattenUrgency.NONE, ("not-a-limit",), (limit,))),
            (OrderIntent, ("run", "agent", "strategy", "decision", "instrument", "SELL", 1, OrderType.MARKET, TimeInForce.DAY, NOW, NOW, NOW, Decimal("1"), Decimal("0"), True)),
            (OrderIntent, ("run", "agent", "strategy", "decision", "instrument", OrderSide.SELL, -1, OrderType.MARKET, TimeInForce.DAY, NOW, NOW, NOW, Decimal("1"), Decimal("0"), True)),
            (OrderIntent, ("run", "agent", "strategy", "decision", "instrument", OrderSide.SELL, 1, "MKT", TimeInForce.DAY, NOW, NOW, NOW, Decimal("1"), Decimal("0"), True)),
            (OrderIntent, ("run", "agent", "strategy", "decision", "instrument", OrderSide.SELL, 1, OrderType.MARKET, TimeInForce.DAY, NOW, NOW, NOW, Decimal("0"), Decimal("0"), True)),
            (OrderIntent, ("run", "agent", "strategy", "decision", "instrument", OrderSide.SELL, 1, OrderType.MARKET, TimeInForce.DAY, NOW + timedelta(minutes=1), NOW, NOW, Decimal("1"), Decimal("0"), True)),
            (OrderIntent, ("run", "agent", "strategy", "decision", "instrument", OrderSide.SELL, 1, OrderType.MARKET, TimeInForce.DAY, NOW, NOW, NOW, Decimal("1"), Decimal("-1"), True)),
            (OrderIntent, ("run", "agent", "strategy", "decision", "instrument", OrderSide.SELL, 1, OrderType.MARKET, TimeInForce.DAY, NOW, NOW, NOW, Decimal("1"), Decimal("0"), 1)),
        )
        for record_type, arguments in cases:
            with self.subTest(record_type=record_type.__name__, arguments=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    record_type(*arguments)

    def test_market_snapshot_normalizes_collections_and_records_are_frozen_and_slotted(self) -> None:
        snapshot = MarketSnapshot(
            decision_time_utc=datetime(2026, 8, 3, 21, tzinfo=UTC),
            rates=[RateObservation("DGS2", "2Y", Decimal("410"), "UST", OBSERVED_AT, AVAILABLE_AT)],
            instruments=[InstrumentObservation(
                "ERIS-YIT", Decimal("99.25"), "ERIS", OBSERVED_AT,
                datetime(2026, 8, 3, 20, 5, tzinfo=UTC),
            )],
            contracts=[ContractMetadata("ERIS-YIT", "2Y", Decimal("42.5"), -1)],
            paper_positions=[PaperPosition("ERIS-YIT", -2)],
            working_orders=[WorkingOrder("order-1", "ERIS-YIT", OrderSide.BUY, 1)],
        )

        self.assertIsInstance(snapshot.rates, tuple)
        self.assertIsInstance(snapshot.instruments, tuple)
        self.assertIsInstance(snapshot.contracts, tuple)
        self.assertIsInstance(snapshot.paper_positions, tuple)
        self.assertIsInstance(snapshot.working_orders, tuple)
        with self.assertRaises(AttributeError):
            snapshot.rates = ()
        with self.assertRaises((AttributeError, TypeError)):
            snapshot.unexpected = "value"

    def test_public_record_field_order_is_stable(self) -> None:
        expected_fields = {
            NamedValue: ("name", "value", "unit"),
            RateObservation: ("series_id", "maturity", "rate_bps", "source", "observed_at_utc", "available_at_utc"),
            InstrumentObservation: ("instrument_id", "price_points", "source", "observed_at_utc", "available_at_utc"),
            ContractMetadata: ("instrument_id", "maturity", "dv01_usd_per_bp", "rate_sensitivity_sign"),
            PaperPosition: ("instrument_id", "quantity_contracts"),
            WorkingOrder: ("order_ref", "instrument_id", "side", "quantity_contracts"),
            MarketSnapshot: ("decision_time_utc", "rates", "instruments", "contracts", "paper_positions", "working_orders"),
            SpreadObservation: ("maturity", "observation_time_utc", "fixed_swap_spread_bps", "expected_funding_spread_bps", "gross_excess_spread_bps", "traditional_cost_buffer_bps", "reverse_cost_buffer_bps", "traditional_net_opportunity_bps", "reverse_net_opportunity_bps", "z_score", "observation_count", "source_quality_ok", "is_fresh"),
            SignalDecision: ("decision_id", "maturity", "decision_time_utc", "prior_state", "new_state", "direction", "reason_code", "feature_values", "strategy_version", "configuration_version"),
            TargetPosition: ("maturity", "swap_instrument_id", "treasury_instrument_id", "swap_quantity_contracts", "treasury_quantity_contracts", "target_dv01_usd_per_bp", "gross_dv01_usd_per_bp", "residual_net_dv01_usd_per_bp", "expected_turnover_contracts", "expected_cost_usd", "rounding_diagnostic", "cap_diagnostic"),
            RiskDecision: ("allowed", "scale", "reason_codes", "flatten_requested", "urgency", "limits", "measured_values"),
            OrderIntent: ("run_id", "agent_id", "strategy_id", "decision_id", "instrument_id", "side", "quantity_contracts", "order_type", "time_in_force", "earliest_submission_utc", "activate_at_utc", "expires_at_utc", "reference_price_points", "max_slippage_price_points", "paper_only"),
        }
        for record_type, expected in expected_fields.items():
            with self.subTest(record_type=record_type.__name__):
                self.assertEqual(tuple(field.name for field in fields(record_type)), expected)

    def test_enum_integer_values_and_declared_domains(self) -> None:
        self.assertEqual((PositionState.REVERSE, PositionState.FLAT, PositionState.TRADITIONAL), (-1, 0, 1))
        self.assertEqual((TradeDirection.REVERSE, TradeDirection.FLAT, TradeDirection.TRADITIONAL), (-1, 0, 1))
        self.assertEqual(OrderSide.BUY.value, "BUY")
        self.assertEqual(OrderSide.SELL.value, "SELL")
        self.assertEqual(OrderType.MARKET.value, "MKT")
        self.assertEqual(OrderType.LIMIT.value, "LMT")
        self.assertEqual(TimeInForce.DAY.value, "DAY")
        self.assertEqual(
            tuple(member.value for member in FlattenUrgency),
            ("none", "scheduled", "emergency"),
        )
        for enum_type in (PositionState, TradeDirection):
            for value in (-2, 2):
                with self.subTest(enum_type=enum_type.__name__, value=value):
                    with self.assertRaises(ValueError):
                        enum_type(value)

    def test_text_fields_reject_blank_and_non_string_values(self) -> None:
        cases = (
            (NamedValue, ("", Decimal("1"), "points")),
            (RateObservation, ("DGS2", " ", Decimal("1"), "UST", OBSERVED_AT, AVAILABLE_AT)),
            (InstrumentObservation, ("ERIS", Decimal("1"), None, OBSERVED_AT, AVAILABLE_AT)),
            (ContractMetadata, ("ERIS", "\n", Decimal("1"), 1)),
            (PaperPosition, ("\t", 1)),
            (WorkingOrder, ("order", "", OrderSide.BUY, 1)),
        )
        for record_type, arguments in cases:
            with self.subTest(record_type=record_type.__name__):
                with self.assertRaises((TypeError, ValueError)):
                    record_type(*arguments)

    def test_datetimes_must_be_exact_utc_and_observations_must_be_causal(self) -> None:
        cases = (
            (RateObservation, ("DGS2", "2Y", Decimal("1"), "UST", datetime(2026, 8, 3, 20), AVAILABLE_AT)),
            (InstrumentObservation, ("ERIS", Decimal("1"), "ERIS", OBSERVED_AT, datetime(2026, 8, 3, 20, tzinfo=timezone(timedelta(hours=1))))),
            (RateObservation, ("DGS2", "2Y", Decimal("1"), "UST", AVAILABLE_AT, OBSERVED_AT)),
            (MarketSnapshot, (datetime(2026, 8, 3, 21), (), (), ())),
        )
        for record_type, arguments in cases:
            with self.subTest(record_type=record_type.__name__, arguments=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    record_type(*arguments)

    def test_decimals_must_be_exact_finite_and_respect_positive_domains(self) -> None:
        cases = (
            (NamedValue, ("signal", float("nan"), "points")),
            (NamedValue, ("signal", Decimal("NaN"), "points")),
            (RateObservation, ("DGS2", "2Y", Decimal("Infinity"), "UST", OBSERVED_AT, AVAILABLE_AT)),
            (InstrumentObservation, ("ERIS", Decimal("0"), "ERIS", OBSERVED_AT, AVAILABLE_AT)),
            (ContractMetadata, ("ERIS", "2Y", Decimal("-1"), 1)),
        )
        for record_type, arguments in cases:
            with self.subTest(record_type=record_type.__name__, arguments=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    record_type(*arguments)

    def test_integer_fields_reject_bools_floats_and_invalid_domains(self) -> None:
        cases = (
            (ContractMetadata, ("ERIS", "2Y", Decimal("1"), 0)),
            (ContractMetadata, ("ERIS", "2Y", Decimal("1"), True)),
            (PaperPosition, ("ERIS", 1.0)),
            (PaperPosition, ("ERIS", True)),
            (WorkingOrder, ("order", "ERIS", OrderSide.BUY, -1)),
            (WorkingOrder, ("order", "ERIS", OrderSide.BUY, 1.0)),
        )
        for record_type, arguments in cases:
            with self.subTest(record_type=record_type.__name__, arguments=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    record_type(*arguments)

    def test_working_order_requires_an_order_side_instance(self) -> None:
        with self.assertRaises(TypeError):
            WorkingOrder("order", "ERIS", "BUY", 1)

    def test_snapshot_requires_declared_nested_types_and_available_data(self) -> None:
        decision_time = datetime(2026, 8, 3, 21, tzinfo=UTC)
        unavailable_rate = RateObservation(
            "DGS2", "2Y", Decimal("1"), "UST", OBSERVED_AT,
            datetime(2026, 8, 3, 21, 1, tzinfo=UTC),
        )
        cases = (
            (("not-a-rate",), (), (), (), ()),
            ((), ("not-an-instrument",), (), (), ()),
            ((), (), ("not-a-contract",), (), ()),
            ((), (), (), ("not-a-position",), ()),
            ((), (), (), (), ("not-an-order",)),
            ((unavailable_rate,), (), (), (), ()),
        )
        for rates, instruments, contracts, positions, orders in cases:
            with self.subTest(rates=rates, instruments=instruments, contracts=contracts, positions=positions, orders=orders):
                with self.assertRaises((TypeError, ValueError)):
                    MarketSnapshot(decision_time, rates, instruments, contracts, positions, orders)


if __name__ == "__main__":
    unittest.main()
