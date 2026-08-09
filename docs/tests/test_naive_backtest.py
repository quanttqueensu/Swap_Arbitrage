import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backtesting import (
    NAIVE_ASSUMPTIONS,
    NaiveAssumptions,
    ReplayEvent,
    StrategyResult,
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
        self.assertEqual(summary["risk_blocked_days"], "1")
        self.assertEqual(summary["missing_input_count"], "1")
        self.assertEqual(dict(result.manifest)["evidence_class"], "synthetic_mechanics_only")

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
            self.assertEqual(manifest["daily_row_count"], "2")
            self.assertEqual(manifest["summary_row_count"], "11")
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
