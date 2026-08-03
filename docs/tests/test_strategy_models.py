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
    TimeInForce,
    TradeDirection,
    WorkingOrder,
)


UTC = timezone.utc
OBSERVED_AT = datetime(2026, 8, 3, 20, tzinfo=UTC)
AVAILABLE_AT = datetime(2026, 8, 3, 20, 30, tzinfo=UTC)


class StrategyModelTests(unittest.TestCase):
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
