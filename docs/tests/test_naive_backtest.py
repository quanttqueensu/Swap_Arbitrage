import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from data_pipeline.contracts import SCHEMAS, validate_csv
from backtesting import (
    NAIVE_ASSUMPTIONS,
    NaiveAssumptions,
    ReplayEvent,
    StrategyResult,
    TradeRecord,
    run_backtest,
    write_results,
)
from strategy import (
    ContractMetadata,
    FlattenUrgency,
    InstrumentObservation,
    MarketSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionState,
    RiskDecision,
    SignalDecision,
    TimeInForce,
    TradeDirection,
)


D = Decimal
UTC = timezone.utc
RUN_ID = "p40-golden"


def when(day):
    return datetime(2026, 1, day, 21, tzinfo=UTC)


def event(day, yit_price="100", zt_price="100", fill_limits=(), extra_prices=()):
    timestamp = when(day)
    prices = (("YITH27", yit_price), ("ZTH27", zt_price), *extra_prices)
    instruments = tuple(
        InstrumentObservation(instrument_id, D(price), "synthetic", timestamp, timestamp)
        for instrument_id, price in prices
    )
    contracts = tuple(
        ContractMetadata(
            instrument_id,
            "2Y",
            D("950") if instrument_id.startswith("ZT") else D("100"),
            -1,
        )
        for instrument_id, _ in prices
    )
    return ReplayEvent(
        MarketSnapshot(timestamp, (), instruments, contracts),
        tuple((instrument_id, D("1000")) for instrument_id, _ in prices),
        fill_limits,
    )


def allowed_risk():
    return RiskDecision(True, D("1"), ("within_limits",), False, FlattenUrgency.NONE, (), ())


def blocked_risk():
    return RiskDecision(False, D("0"), ("stale_market_data",), False, FlattenUrgency.NONE, (), ())


def decision(
    timestamp,
    decision_id="decision-1",
    reason="enter_traditional",
    prior_state=PositionState.FLAT,
    new_state=PositionState.TRADITIONAL,
    direction=TradeDirection.TRADITIONAL,
):
    return SignalDecision(
        decision_id,
        "2Y",
        timestamp,
        prior_state,
        new_state,
        direction,
        reason,
        (),
        "p10.strategy-equations.v1",
        "p40.naive.v1",
    )


def intent(timestamp, instrument_id, side, quantity, decision_id="decision-1"):
    return OrderIntent(
        RUN_ID,
        "backtest",
        "swap-arbitrage",
        decision_id,
        instrument_id,
        side,
        quantity,
        OrderType.MARKET,
        TimeInForce.DAY,
        timestamp,
        timestamp,
        timestamp + timedelta(days=2),
        D("100"),
        D("0.01"),
        True,
    )


class NaiveBoundaryTests(unittest.TestCase):
    # Mutation caught: accepting a negative fixed cost or changing the frozen commission.
    def test_naive_assumptions_fail_closed(self):
        self.assertEqual(NAIVE_ASSUMPTIONS.commission_usd_per_contract, D("1"))
        with self.assertRaises(ValueError):
            NaiveAssumptions(D("-0.01"), D("1"), D("0"), D("0"), D("0"))

        self.assertTrue(hasattr(ReplayEvent, "__dataclass_fields__"))
        self.assertEqual(StrategyResult(), StrategyResult())


class NaiveReplayTests(unittest.TestCase):
    # Mutation caught: filling on the decision event or treating zero capacity as a fill.
    def test_warmup_order_timing_partial_and_rejected_fills(self):
        def strategy(snapshot):
            if snapshot.decision_time_utc != when(2):
                return StrategyResult()
            signal = decision(snapshot.decision_time_utc)
            return StrategyResult(
                (signal,),
                (("2Y", allowed_risk()),),
                (
                    intent(snapshot.decision_time_utc, "YITH27", OrderSide.BUY, 4),
                    intent(snapshot.decision_time_utc, "ZTH27", OrderSide.SELL, 1),
                ),
            )

        result = run_backtest(
            RUN_ID,
            (event(1), event(2), event(3, fill_limits=(("YITH27", 2), ("ZTH27", 0)))),
            strategy,
        )

        self.assertEqual([row.observation_date for row in result.daily], [
            "2026-01-01", "2026-01-02", "2026-01-03",
        ])
        self.assertEqual(len(result.orders), 2)
        self.assertEqual([fill.status for fill in result.fills], ["partial", "rejected"])
        self.assertEqual(result.fills[0].fill_time_utc, when(3))
        self.assertEqual(result.fills[0].filled_quantity_contracts, 2)
        self.assertEqual(result.fills[0].remaining_quantity_contracts, 2)
        self.assertEqual(result.fills[1].filled_quantity_contracts, 0)
        self.assertEqual(
            [(row.instrument_id, row.quantity_contracts) for row in result.positions],
            [("YITH27", 2)],
        )
        self.assertEqual(len(result.trades), 1)
        self.assertIsNone(result.trades[0].closed_at_utc)
        self.assertEqual(result.trades[0].cost_usd, D("32.000"))

    # Mutation caught: marking today's fills for yesterday or omitting financing/costs.
    def test_entry_exit_accounting_identity_equity_and_drawdown(self):
        def strategy(snapshot):
            timestamp = snapshot.decision_time_utc
            if timestamp == when(2):
                signal = decision(timestamp)
                intents = (
                    intent(timestamp, "YITH27", OrderSide.BUY, 2),
                    intent(timestamp, "ZTH27", OrderSide.SELL, 1),
                )
            elif timestamp == when(4):
                signal = decision(
                    timestamp,
                    "decision-2",
                    "exit_traditional",
                    PositionState.TRADITIONAL,
                    PositionState.FLAT,
                    TradeDirection.FLAT,
                )
                intents = (
                    intent(timestamp, "YITH27", OrderSide.SELL, 2, "decision-2"),
                    intent(timestamp, "ZTH27", OrderSide.BUY, 1, "decision-2"),
                )
            else:
                return StrategyResult()
            return StrategyResult((signal,), (("2Y", allowed_risk()),), intents)

        result = run_backtest(
            RUN_ID,
            (
                event(1),
                event(2),
                event(3),
                event(4, "100.1", "99.98"),
                event(5, "100.1", "99.98"),
            ),
            strategy,
        )

        by_date = {row.observation_date: row for row in result.daily}
        self.assertEqual(by_date["2026-01-03"].transaction_cost_usd, D("48"))
        self.assertEqual(by_date["2026-01-04"].gross_pnl_usd, D("220.00"))
        self.assertEqual(by_date["2026-01-04"].financing_cost_usd, D("0.30"))
        self.assertEqual(by_date["2026-01-04"].equity_usd, D("1000171.70"))
        self.assertEqual(by_date["2026-01-05"].transaction_cost_usd, D("48"))
        self.assertEqual(by_date["2026-01-05"].drawdown_usd, D("48.30"))
        self.assertFalse(any(row.timestamp_utc == when(5) for row in result.positions))
        for row in result.daily:
            self.assertEqual(
                row.net_pnl_usd,
                row.gross_pnl_usd - row.transaction_cost_usd - row.financing_cost_usd,
            )

    # Mutation caught: netting a reversal to entry quantity instead of charging both actions.
    def test_direct_reversal_charges_exit_and_entry_turnover(self):
        def strategy(snapshot):
            timestamp = snapshot.decision_time_utc
            if timestamp == when(2):
                signal = decision(timestamp)
                intents = (
                    intent(timestamp, "YITH27", OrderSide.BUY, 2),
                    intent(timestamp, "ZTH27", OrderSide.SELL, 1),
                )
            elif timestamp == when(4):
                signal = decision(
                    timestamp,
                    "decision-2",
                    "exit_traditional_enter_reverse",
                    PositionState.TRADITIONAL,
                    PositionState.REVERSE,
                    TradeDirection.REVERSE,
                )
                intents = (
                    intent(timestamp, "YITH27", OrderSide.SELL, 4, "decision-2"),
                    intent(timestamp, "ZTH27", OrderSide.BUY, 2, "decision-2"),
                )
            else:
                return StrategyResult()
            return StrategyResult((signal,), (("2Y", allowed_risk()),), intents)

        result = run_backtest(
            RUN_ID,
            (event(1), event(2), event(3), event(4), event(5)),
            strategy,
        )
        day_five = result.daily[-1]
        self.assertEqual(day_five.transaction_cost_usd, D("96"))
        self.assertEqual(
            [(row.instrument_id, row.quantity_contracts) for row in result.positions if row.timestamp_utc == when(5)],
            [("YITH27", -2), ("ZTH27", 1)],
        )
        self.assertEqual([row.direction for row in result.trades], [1, -1])
        self.assertEqual(result.trades[0].closed_at_utc, when(5))
        self.assertIsNone(result.trades[1].closed_at_utc)
        self.assertEqual(
            sum((row.cost_usd for row in result.trades), D("0")),
            D("144.600"),
        )

    # Mutation caught: opening a zero-exposure reverse trade after only the closing orders fill.
    def test_reversal_with_rejected_opening_orders_has_no_phantom_trade(self):
        def strategy(snapshot):
            timestamp = snapshot.decision_time_utc
            if timestamp == when(2):
                signal = decision(timestamp)
                intents = (
                    intent(timestamp, "YITH27", OrderSide.BUY, 2),
                    intent(timestamp, "ZTH27", OrderSide.SELL, 1),
                )
            elif timestamp == when(4):
                signal = decision(
                    timestamp,
                    "decision-2",
                    "exit_traditional_enter_reverse",
                    PositionState.TRADITIONAL,
                    PositionState.REVERSE,
                    TradeDirection.REVERSE,
                )
                intents = (
                    intent(timestamp, "YITH27", OrderSide.SELL, 2, "decision-2"),
                    intent(timestamp, "ZTH27", OrderSide.BUY, 1, "decision-2"),
                    intent(timestamp, "YITH27", OrderSide.SELL, 2, "decision-2"),
                    intent(timestamp, "ZTH27", OrderSide.BUY, 1, "decision-2"),
                )
            else:
                return StrategyResult()
            return StrategyResult((signal,), (("2Y", allowed_risk()),), intents)

        result = run_backtest(
            RUN_ID,
            (
                event(1),
                event(2),
                event(3),
                event(4),
                event(5, fill_limits=(("YITH27", 2), ("ZTH27", 1))),
            ),
            strategy,
        )

        self.assertFalse(any(row.timestamp_utc == when(5) for row in result.positions))
        self.assertEqual([fill.status for fill in result.fills[-4:]], [
            "filled", "filled", "rejected", "rejected",
        ])
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].closed_at_utc, when(5))

    # Mutation caught: assigning both sides' P&L to the old trade during a partial reversal.
    def test_partial_reversal_attributes_each_held_instrument_to_its_trade(self):
        def strategy(snapshot):
            timestamp = snapshot.decision_time_utc
            if timestamp == when(2):
                signal = decision(timestamp)
                intents = (
                    intent(timestamp, "YITH27", OrderSide.BUY, 2),
                    intent(timestamp, "ZTH27", OrderSide.SELL, 2),
                )
            elif timestamp == when(4):
                signal = decision(
                    timestamp,
                    "decision-2",
                    "exit_traditional_enter_reverse",
                    PositionState.TRADITIONAL,
                    PositionState.REVERSE,
                    TradeDirection.REVERSE,
                )
                intents = (
                    intent(timestamp, "YITH27", OrderSide.SELL, 4, "decision-2"),
                    intent(timestamp, "ZTH27", OrderSide.BUY, 4, "decision-2"),
                )
            else:
                return StrategyResult()
            return StrategyResult((signal,), (("2Y", allowed_risk()),), intents)

        result = run_backtest(
            RUN_ID,
            (
                event(1),
                event(2),
                event(3),
                event(4),
                event(5, fill_limits=(("YITH27", 4), ("ZTH27", 1))),
                event(6, "99", "101"),
            ),
            strategy,
        )

        self.assertEqual([row.direction for row in result.trades], [1, -1])
        self.assertEqual(result.trades[0].closed_at_utc, when(6))
        self.assertIsNone(result.trades[1].closed_at_utc)
        self.assertEqual(result.trades[0].gross_pnl_usd, D("-1000"))
        self.assertEqual(result.trades[1].gross_pnl_usd, D("2000"))

    # Mutation caught: treating roll close/open as ordinary fills without both roll charges.
    def test_roll_close_and_open_charge_each_filled_contract(self):
        def strategy(snapshot):
            timestamp = snapshot.decision_time_utc
            if timestamp == when(2):
                signal = decision(timestamp)
                intents = (
                    intent(timestamp, "YITH27", OrderSide.BUY, 2),
                    intent(timestamp, "ZTH27", OrderSide.SELL, 1),
                )
            elif timestamp == when(4):
                signal = decision(
                    timestamp,
                    "decision-2",
                    "roll_close_open",
                    PositionState.TRADITIONAL,
                    PositionState.TRADITIONAL,
                    TradeDirection.TRADITIONAL,
                )
                intents = (
                    intent(timestamp, "YITH27", OrderSide.SELL, 2, "decision-2"),
                    intent(timestamp, "YITM27", OrderSide.BUY, 2, "decision-2"),
                )
            else:
                return StrategyResult()
            return StrategyResult((signal,), (("2Y", allowed_risk()),), intents)

        events = tuple(event(day, extra_prices=(("YITM27", "99"),)) for day in range(1, 6))
        result = run_backtest(RUN_ID, events, strategy)
        self.assertEqual(result.daily[-1].transaction_cost_usd, D("68"))
        self.assertEqual(sum(fill.roll_cost_usd for fill in result.fills), D("4"))
        self.assertEqual(
            [(row.instrument_id, row.quantity_contracts) for row in result.positions if row.timestamp_utc == when(5)],
            [("YITM27", 2), ("ZTH27", -1)],
        )
        self.assertEqual(len(result.trades), 1)
        self.assertIsNone(result.trades[0].closed_at_utc)
        self.assertEqual(result.trades[0].cost_usd, D("116.600"))

    # Mutation caught: leaking pre-window state or revising prior rows after future data arrives.
    def test_date_window_starts_flat_and_future_data_cannot_revise_history(self):
        def strategy(snapshot):
            if snapshot.decision_time_utc != when(2):
                return StrategyResult()
            signal = decision(snapshot.decision_time_utc)
            return StrategyResult(
                (signal,),
                (("2Y", allowed_risk()),),
                (intent(snapshot.decision_time_utc, "YITH27", OrderSide.BUY, 1),),
            )

        base_events = (event(1), event(2), event(3), event(4, "100.1"))
        base = run_backtest(RUN_ID, base_events, strategy)
        extended = run_backtest(RUN_ID, (*base_events, event(5, "100.2")), strategy)
        windowed = run_backtest(
            RUN_ID,
            base_events,
            strategy,
            start_date=when(3).date(),
            end_date=when(4).date(),
        )

        self.assertEqual(base.daily, extended.daily[: len(base.daily)])
        self.assertEqual(base.decisions, extended.decisions[: len(base.decisions)])
        self.assertEqual(base.fills, extended.fills[: len(base.fills)])
        self.assertEqual([row.observation_date for row in windowed.daily], [
            "2026-01-03", "2026-01-04",
        ])
        self.assertEqual(windowed.positions, ())
        self.assertEqual(dict(windowed.manifest)["window_policy"], "start_flat")

    # Mutation caught: silently treating a missing held-position mark as complete data.
    def test_risk_blocked_dates_and_missing_marks_are_counted(self):
        def strategy(snapshot):
            timestamp = snapshot.decision_time_utc
            if timestamp == when(1):
                signal = decision(timestamp, reason="data_not_ready")
                return StrategyResult((signal,), (("2Y", blocked_risk()),), ())
            if timestamp == when(2):
                signal = decision(timestamp)
                return StrategyResult(
                    (signal,),
                    (("2Y", allowed_risk()),),
                    (intent(timestamp, "YITH27", OrderSide.BUY, 1),),
                )
            return StrategyResult()

        missing_time = when(4)
        missing_event = ReplayEvent(
            MarketSnapshot(
                missing_time,
                (),
                (InstrumentObservation("ZTH27", D("100"), "synthetic", missing_time, missing_time),),
                (ContractMetadata("ZTH27", "2Y", D("950"), -1),),
            ),
            (("ZTH27", D("1000")),),
        )
        result = run_backtest(RUN_ID, (event(1), event(2), event(3), missing_event), strategy)
        summary = dict(result.summary)
        missing_day = result.daily[-1]
        self.assertEqual(summary["risk_blocked_days"], "1")
        self.assertEqual(summary["missing_input_count"], "1")
        self.assertEqual(missing_day.gross_pnl_usd, D("0"))
        self.assertEqual(missing_day.financing_cost_usd, D("0.10"))
        self.assertEqual(
            dict(result.manifest)["missing_input_locations"],
            ";".join((
                "2026-01-04:YITH27:contract_metadata",
                "2026-01-04:YITH27:current_mark",
                "2026-01-04:YITH27:current_multiplier",
            )),
        )
        self.assertEqual(dict(result.manifest)["evidence_class"], "synthetic_mechanics_only")
        self.assertEqual(dict(result.manifest)["maturity_scope"], "synthetic_fixture")

    # Mutation caught: granting the full event capacity separately to sibling orders.
    def test_fill_capacity_is_shared_by_same_instrument_orders(self):
        def strategy(snapshot):
            if snapshot.decision_time_utc != when(2):
                return StrategyResult()
            first = decision(snapshot.decision_time_utc, "decision-1")
            second = decision(snapshot.decision_time_utc, "decision-2")
            return StrategyResult(
                (first, second),
                (("2Y", allowed_risk()),),
                (
                    intent(snapshot.decision_time_utc, "YITH27", OrderSide.BUY, 2, "decision-1"),
                    intent(snapshot.decision_time_utc, "YITH27", OrderSide.BUY, 2, "decision-2"),
                ),
            )

        result = run_backtest(
            RUN_ID,
            (event(1), event(2), event(3, fill_limits=(("YITH27", 3),))),
            strategy,
        )
        self.assertEqual([fill.filled_quantity_contracts for fill in result.fills], [2, 1])
        self.assertEqual(sum(fill.filled_quantity_contracts for fill in result.fills), 3)

    # Mutation caught: dropping an expired working order without an auditable terminal row.
    def test_expired_order_emits_zero_quantity_terminal_record(self):
        def strategy(snapshot):
            if snapshot.decision_time_utc != when(1):
                return StrategyResult()
            signal = decision(snapshot.decision_time_utc)
            expiring = intent(snapshot.decision_time_utc, "YITH27", OrderSide.BUY, 1)
            expiring = OrderIntent(
                expiring.run_id,
                expiring.agent_id,
                expiring.strategy_id,
                expiring.decision_id,
                expiring.instrument_id,
                expiring.side,
                expiring.quantity_contracts,
                expiring.order_type,
                expiring.time_in_force,
                expiring.earliest_submission_utc,
                when(3),
                when(3),
                expiring.reference_price_points,
                expiring.max_slippage_price_points,
                expiring.paper_only,
            )
            return StrategyResult((signal,), (("2Y", allowed_risk()),), (expiring,))

        result = run_backtest(RUN_ID, (event(1), event(2), event(4)), strategy)
        self.assertEqual(len(result.fills), 1)
        self.assertEqual(result.fills[0].status, "expired")
        self.assertEqual(result.fills[0].filled_quantity_contracts, 0)
        self.assertEqual(result.positions, ())

    # Mutation caught: silently overwriting duplicate marks/contracts or producing duplicate daily keys.
    def test_duplicate_event_keys_and_observation_dates_fail_closed(self):
        base = event(1)
        with self.assertRaises(ValueError):
            ReplayEvent(
                MarketSnapshot(
                    base.snapshot.decision_time_utc,
                    (),
                    (base.snapshot.instruments[0], base.snapshot.instruments[0]),
                    (base.snapshot.contracts[0],),
                ),
                base.multipliers_usd_per_point,
            )
        later_time = when(1) + timedelta(hours=1)
        later = ReplayEvent(
            MarketSnapshot(
                later_time,
                (),
                tuple(
                    InstrumentObservation(
                        item.instrument_id,
                        item.price_points,
                        item.source,
                        item.observed_at_utc,
                        item.available_at_utc,
                    )
                    for item in base.snapshot.instruments
                ),
                base.snapshot.contracts,
            ),
            base.multipliers_usd_per_point,
        )
        with self.assertRaises(ValueError):
            run_backtest(RUN_ID, (base, later), lambda snapshot: StrategyResult())


class NaiveReportTests(unittest.TestCase):
    # Mutation caught: hashing only a version label while different run assumptions stay invisible.
    def test_decision_config_hash_fingerprints_effective_run_configuration(self):
        def strategy(snapshot):
            signal = decision(snapshot.decision_time_utc)
            return StrategyResult((signal,), (("2Y", allowed_risk()),), ())

        first = run_backtest(RUN_ID, (event(1),), strategy)
        second = run_backtest(
            RUN_ID,
            (event(1),),
            strategy,
            assumptions=replace(NAIVE_ASSUMPTIONS, commission_usd_per_contract=D("2")),
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_dir = write_results(first, root / "first")
            second_dir = write_results(second, root / "second")
            with (first_dir / "decisions.csv").open(newline="", encoding="utf-8") as handle:
                first_hash = next(csv.DictReader(handle))["config_hash"]
            with (second_dir / "decisions.csv").open(newline="", encoding="utf-8") as handle:
                second_hash = next(csv.DictReader(handle))["config_hash"]

        self.assertNotEqual(first_hash, second_hash)

    # Mutation caught: writing multi-maturity trades in close order instead of canonical open order.
    def test_trade_report_sorts_by_open_time_before_validation(self):
        result = run_backtest(RUN_ID, (event(1), event(2)), lambda snapshot: StrategyResult())
        later_open = TradeRecord(
            "trade-2", "decision-2", "5Y", 1, when(3), when(4), D("0"), D("0"), D("0")
        )
        earlier_open = TradeRecord(
            "trade-1", "decision-1", "2Y", 1, when(2), when(5), D("0"), D("0"), D("0")
        )
        result = replace(result, trades=(later_open, earlier_open))

        with TemporaryDirectory() as temp_dir:
            run_dir = write_results(result, Path(temp_dir))
            with (run_dir / "trades.csv").open(newline="", encoding="utf-8") as handle:
                self.assertEqual(
                    [row["trade_id"] for row in csv.DictReader(handle)],
                    ["trade-1", "trade-2"],
                )

    # Mutation caught: validating reports against their own dataclasses instead of the approved catalog.
    def test_generated_reports_match_approved_schema_catalog(self):
        def strategy(snapshot):
            timestamp = snapshot.decision_time_utc
            if timestamp == when(2):
                signal = decision(timestamp)
                intents = (
                    intent(timestamp, "YITH27", OrderSide.BUY, 2),
                    intent(timestamp, "ZTH27", OrderSide.SELL, 1),
                )
            elif timestamp == when(4):
                signal = decision(
                    timestamp,
                    "decision-2",
                    "exit_traditional",
                    PositionState.TRADITIONAL,
                    PositionState.FLAT,
                    TradeDirection.FLAT,
                )
                intents = (
                    intent(timestamp, "YITH27", OrderSide.SELL, 2, "decision-2"),
                    intent(timestamp, "ZTH27", OrderSide.BUY, 1, "decision-2"),
                )
            else:
                return StrategyResult()
            return StrategyResult((signal,), (("2Y", allowed_risk()),), intents)

        result = run_backtest(
            RUN_ID,
            (event(1), event(2), event(3), event(4, "100.1", "99.98"), event(5, "100.1", "99.98")),
            strategy,
        )
        schema_ids = {
            "daily.csv": "backtest_daily",
            "decisions.csv": "backtest_decisions",
            "orders.csv": "backtest_orders",
            "fills.csv": "backtest_fills",
            "trades.csv": "backtest_trades",
            "positions.csv": "backtest_positions",
            "summary.csv": "backtest_summary",
        }

        with TemporaryDirectory() as temp_dir:
            run_dir = write_results(result, Path(temp_dir))
            for filename, schema_id in schema_ids.items():
                with self.subTest(filename=filename):
                    self.assertGreaterEqual(validate_csv(SCHEMAS[schema_id], run_dir / filename), 0)

            with (run_dir / "fills.csv").open(newline="", encoding="utf-8") as handle:
                fill_rows = list(csv.DictReader(handle))
            self.assertEqual([row["quantity"] for row in fill_rows], ["2", "-1", "-2", "1"])
            self.assertEqual([row["commission_usd"] for row in fill_rows], ["2", "1", "2", "1"])

            with (run_dir / "trades.csv").open(newline="", encoding="utf-8") as handle:
                trade_rows = list(csv.DictReader(handle))
            self.assertEqual(len(trade_rows), 1)
            self.assertEqual({
                key: trade_rows[0][key]
                for key in (
                    "trade_id",
                    "decision_id",
                    "maturity",
                    "direction",
                    "opened_at_utc",
                    "closed_at_utc",
                )
            }, {
                "trade_id": "trade-1",
                "decision_id": "decision-1",
                "maturity": "2Y",
                "direction": "1",
                "opened_at_utc": "2026-01-03T21:00:00Z",
                "closed_at_utc": "2026-01-05T21:00:00Z",
            })
            self.assertEqual(D(trade_rows[0]["gross_pnl_usd"]), D("220.00"))
            self.assertEqual(D(trade_rows[0]["cost_usd"]), D("96.60"))
            self.assertEqual(D(trade_rows[0]["net_pnl_usd"]), D("123.40"))

            with (run_dir / "positions.csv").open(newline="", encoding="utf-8") as handle:
                position_rows = {
                    (row["observation_date"], row["instrument_id"]): row
                    for row in csv.DictReader(handle)
                }
            day_four_yit = position_rows[("2026-01-04", "YITH27")]
            day_four_zt = position_rows[("2026-01-04", "ZTH27")]
            self.assertEqual(D(day_four_yit["market_value_usd"]), D("200200"))
            self.assertEqual(D(day_four_yit["unrealized_pnl_usd"]), D("170"))
            self.assertEqual(D(day_four_zt["market_value_usd"]), D("-99980"))
            self.assertEqual(D(day_four_zt["unrealized_pnl_usd"]), D("5"))

            with (run_dir / "summary.csv").open(newline="", encoding="utf-8") as handle:
                summary_rows = list(csv.DictReader(handle))
            self.assertEqual(len(summary_rows), 1)
            self.assertEqual(summary_rows[0]["run_id"], RUN_ID)
            self.assertEqual(summary_rows[0]["row_count"], "5")
            self.assertEqual(summary_rows[0]["trade_count"], "1")
            self.assertEqual(D(summary_rows[0]["net_pnl_usd"]), D("123.40"))

    # Mutation caught: widening daily.csv, omitting an artifact, or making reruns nondeterministic.
    def test_writes_exact_validated_csv_set_deterministically(self):
        result = run_backtest(RUN_ID, (event(1), event(2)), lambda snapshot: StrategyResult())
        with TemporaryDirectory() as temp_dir:
            run_dir = write_results(result, Path(temp_dir))
            expected = {
                "manifest.csv",
                "daily.csv",
                "decisions.csv",
                "orders.csv",
                "fills.csv",
                "trades.csv",
                "positions.csv",
                "summary.csv",
            }
            self.assertEqual({path.name for path in run_dir.iterdir()}, expected)
            with (run_dir / "daily.csv").open(newline="", encoding="utf-8") as handle:
                self.assertEqual(next(csv.reader(handle)), [
                    "observation_date",
                    "gross_pnl_usd",
                    "transaction_cost_usd",
                    "financing_cost_usd",
                    "net_pnl_usd",
                    "equity_usd",
                    "drawdown_usd",
                    "drawdown_pct",
                    "gross_dv01_usd_per_bp",
                    "net_dv01_usd_per_bp",
                ])
            first = {path.name: path.read_bytes() for path in run_dir.iterdir()}
            with (run_dir / "manifest.csv").open(newline="", encoding="utf-8") as handle:
                manifest_rows = list(csv.DictReader(handle))
                manifest = {row["key"]: row["value"] for row in manifest_rows}
            self.assertEqual(manifest["schema_version"], "p40.backtest.v1")
            self.assertEqual(manifest["maturity_scope"], "synthetic_fixture")
            self.assertNotIn("complete_2y_5y", manifest.values())
            self.assertEqual(manifest["daily_row_count"], "2")
            self.assertEqual(manifest["summary_row_count"], "1")
            self.assertEqual(manifest["manifest_row_count"], str(len(manifest_rows)))
            self.assertEqual(manifest["coverage_start_date"], "2026-01-01")
            self.assertEqual(
                manifest["daily_sha256"],
                hashlib.sha256((run_dir / "daily.csv").read_bytes()).hexdigest(),
            )
            write_results(result, Path(temp_dir))
            second = {path.name: path.read_bytes() for path in run_dir.iterdir()}
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
