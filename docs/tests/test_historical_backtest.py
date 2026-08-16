from dataclasses import replace
from contextlib import redirect_stderr, redirect_stdout
from datetime import timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from backtesting import NAIVE_ASSUMPTIONS, NaiveAssumptions, run_historical_backtest
from backtesting.__main__ import main, parse_args
from backtesting.historical import _events_from_frame, _historical_strategy
from strategy import PaperPosition


D = Decimal


def historical_frame():
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]),
        "risk_allowed": [1, 1, 1, 1],
        "risk_block_reason": ["", "", "", ""],
        "proxy_position_2y": [0, 1, 1, 0],
        "swap_futures_contracts_rounded_2y": [0, 2, 2, 0],
        "treasury_futures_contracts_rounded_2y": [0, -1, -1, 0],
        "swap_ticker_2y": ["YITH24"] * 4,
        "treasury_ticker_2y": ["ZTH24"] * 4,
        "swap_price_2y": [100.0, 100.0, 100.1, 100.1],
        "treasury_price_2y": [102.0, 102.0, 101.99, 101.99],
        "swap_dv01_per_contract_2y": [19.0] * 4,
        "treasury_dv01_per_contract_2y": [38.0] * 4,
    })


def holding_frame():
    frame = historical_frame()
    frame.loc[2, "swap_price_2y"] = 100.0
    frame.loc[3, "swap_price_2y"] = 100.11
    frame.loc[3, [
        "swap_futures_contracts_rounded_2y",
        "treasury_futures_contracts_rounded_2y",
    ]] = 0
    return pd.concat([
        frame,
        frame.iloc[[-1]].assign(date=pd.Timestamp("2024-01-08")),
    ], ignore_index=True)


class HistoricalEventTests(unittest.TestCase):
    def test_rows_become_ordered_typed_events(self):
        events = _events_from_frame(historical_frame())

        self.assertEqual(len(events), 4)
        self.assertEqual(events[0].snapshot.decision_time_utc.tzinfo, timezone.utc)
        self.assertEqual(
            dict(events[0].multipliers_usd_per_point),
            {"YITH24": D("1000.0"), "ZTH24": D("2000.0")},
        )
        self.assertEqual(
            [item.instrument_id for item in events[0].snapshot.contracts],
            ["YITH24", "ZTH24"],
        )

    def test_duplicate_dates_and_invalid_active_fields_fail_closed(self):
        duplicate = pd.concat([historical_frame(), historical_frame().iloc[[0]]])
        with self.assertRaisesRegex(RuntimeError, "duplicate date"):
            _events_from_frame(duplicate)

        invalid = historical_frame()
        invalid.loc[1, "swap_price_2y"] = float("nan")
        with self.assertRaisesRegex(RuntimeError, "positive price/DV01 and ticker"):
            _events_from_frame(invalid)

    # Mutation caught: allowing active swap or Treasury contracts through with
    # a missing or nonpositive per-contract DV01.
    def test_active_contracts_require_positive_swap_and_treasury_dv01(self):
        for column, value in (
            ("swap_dv01_per_contract_2y", float("nan")),
            ("swap_dv01_per_contract_2y", 0.0),
            ("treasury_dv01_per_contract_2y", float("nan")),
            ("treasury_dv01_per_contract_2y", 0.0),
        ):
            with self.subTest(column=column, value=value):
                invalid = historical_frame()
                invalid.loc[1, column] = value

                with self.assertRaisesRegex(RuntimeError, "positive price/DV01 and ticker"):
                    _events_from_frame(invalid)

    def test_conflicting_duplicate_instruments_fail_closed(self):
        conflicting = historical_frame()
        conflicting["swap_ticker_5y"] = "YITH24"
        conflicting["swap_price_5y"] = 100.0
        conflicting["swap_dv01_per_contract_5y"] = 19.0

        with self.assertRaisesRegex(RuntimeError, "conflicting duplicate instrument"):
            _events_from_frame(conflicting)

    def test_nonnumeric_contract_quantity_fails_closed(self):
        invalid = historical_frame()
        invalid["swap_futures_contracts_rounded_2y"] = invalid[
            "swap_futures_contracts_rounded_2y"
        ].astype(object)
        invalid.loc[1, "swap_futures_contracts_rounded_2y"] = "not-a-quantity"

        with self.assertRaisesRegex(RuntimeError, "integer contract quantity"):
            _events_from_frame(invalid)

    def test_fractional_contract_quantity_fails_closed(self):
        invalid = historical_frame()
        invalid["swap_futures_contracts_rounded_2y"] = invalid[
            "swap_futures_contracts_rounded_2y"
        ].astype(float)
        invalid.loc[1, "swap_futures_contracts_rounded_2y"] = 1.5

        with self.assertRaisesRegex(RuntimeError, "integer contract quantity"):
            _events_from_frame(invalid)


class HistoricalRunTests(unittest.TestCase):
    def _manifest_for(self, frame: pd.DataFrame) -> dict[str, str]:
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=frame):
                result, _ = run_historical_backtest("historical-provenance", Path(directory))
        return dict(result.manifest)

    # Mutation caught: deriving historical provenance from replay market events
    # alone, leaving the selected target quantities unauditable.
    def test_historical_target_changes_change_input_provenance(self):
        baseline = self._manifest_for(historical_frame())
        changed_target = historical_frame()
        changed_target.loc[1, "swap_futures_contracts_rounded_2y"] = 3
        changed = self._manifest_for(changed_target)

        self.assertNotEqual(baseline["input_sha256"], changed["input_sha256"])

    # Mutation caught: omitting the effective upstream risk state or its reason
    # from the provenance of a historical replay.
    def test_historical_risk_state_and_reason_change_input_provenance(self):
        baseline = self._manifest_for(historical_frame())
        blocked = historical_frame()
        blocked.loc[1, "risk_allowed"] = 0
        blocked.loc[1, "risk_block_reason"] = "portfolio:net_dv01_limit"
        changed_state = self._manifest_for(blocked)
        changed_reason = blocked.copy()
        changed_reason.loc[1, "risk_block_reason"] = "freshness:market_data"
        changed_reason = self._manifest_for(changed_reason)

        self.assertNotEqual(baseline["input_sha256"], changed_state["input_sha256"])
        self.assertNotEqual(changed_state["input_sha256"], changed_reason["input_sha256"])

    # Mutation caught: retaining the synthetic engine labels on a historical
    # signal/risk proxy replay.
    def test_historical_manifest_uses_research_proxy_labels(self):
        manifest = self._manifest_for(historical_frame())

        self.assertEqual(manifest["maturity_scope"], "historical_signal_risk_proxy")
        self.assertEqual(manifest["evidence_class"], "historical_research_proxy_only")

    # Mutation caught: deriving active backtest targets from proxy exposure
    # instead of the rounded swap contracts that drive executable orders.
    def test_rounded_swap_contracts_define_the_active_historical_target(self):
        frame = historical_frame()
        frame.loc[:, "proxy_position_2y"] = 0
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=frame):
                result, _ = run_historical_backtest("rounded-target", Path(directory))

        self.assertEqual(
            [item.decision_time_utc.date().isoformat() for item in result.decisions],
            ["2024-01-03", "2024-01-05"],
        )

    # Mutation caught: executing target orders on their decision event or marking
    # P&L before the position is held.
    def test_targets_fill_later_and_pnl_uses_only_held_positions(self):
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=historical_frame()):
                result, run_dir = run_historical_backtest(
                    "historical-golden", Path(directory), assumptions=NAIVE_ASSUMPTIONS
                )

            self.assertEqual(
                [fill.fill_time_utc.date().isoformat() for fill in result.fills[:2]],
                ["2024-01-04", "2024-01-04"],
            )
            self.assertEqual(result.daily[1].gross_pnl_usd, D("0"))
            self.assertEqual(result.daily[2].gross_pnl_usd, D("0"))
            self.assertEqual(result.daily[3].gross_pnl_usd, D("0"))
            self.assertEqual(
                sorted(path.name for path in run_dir.iterdir()),
                [
                    "daily.csv", "decisions.csv", "fills.csv", "manifest.csv",
                    "orders.csv", "positions.csv", "summary.csv", "trades.csv",
                ],
            )

    # Mutation caught: accepting a blocked target that increases a held position.
    def test_risk_block_can_flatten_but_cannot_open_exposure(self):
        frame = historical_frame()
        frame.loc[2, "risk_allowed"] = 0
        frame.loc[2, "risk_block_reason"] = "portfolio:net_dv01_limit"
        frame.loc[2, "swap_futures_contracts_rounded_2y"] = 0
        frame.loc[2, "treasury_futures_contracts_rounded_2y"] = 0
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=frame):
                result, _ = run_historical_backtest("risk-flatten", Path(directory))

        self.assertTrue(any(item.reason_code == "risk_flatten" for item in result.decisions))
        self.assertFalse(any(fill.remaining_quantity_contracts for fill in result.fills))
        self.assertEqual(dict(result.manifest)["risk_blocked_days"], "1")

    # Mutation caught: treating the upstream pipe-separated reason string as one code.
    def test_risk_block_preserves_each_upstream_reason_code(self):
        frame = historical_frame()
        frame.loc[2, "risk_allowed"] = 0
        frame.loc[2, "risk_block_reason"] = "portfolio:net_dv01_limit|freshness:market_data"
        events = _events_from_frame(frame)
        snapshot = replace(
            events[2].snapshot,
            paper_positions=(PaperPosition("YITH24", 2), PaperPosition("ZTH24", -1)),
        )

        result = _historical_strategy("risk-reasons", frame, NAIVE_ASSUMPTIONS)(snapshot)

        self.assertEqual(
            dict(result.risk_decisions)["2Y"].reason_codes,
            ("portfolio:net_dv01_limit", "freshness:market_data", "flatten_only"),
        )

    # Mutation caught: losing a retiring mark before the causal close can fill.
    def test_roll_uses_explicit_zero_return_retiring_mark_policy(self):
        frame = historical_frame()
        frame.loc[2:, "swap_ticker_2y"] = "YITM24"
        frame.loc[2:, "swap_price_2y"] = 125.0
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=frame):
                result, _ = run_historical_backtest("roll", Path(directory))

        self.assertEqual(
            dict(result.manifest)["historical_roll_mark_policy"],
            "last_pre_roll_mark_zero_return",
        )
        self.assertTrue(any("roll" in item.reason_code for item in result.decisions))
        self.assertEqual(result.daily[3].gross_pnl_usd, D("0"))
        roll_fills = [
            item for item in result.fills
            if item.decision_id == "historical-2024-01-04-2y"
        ]
        self.assertEqual(
            [
                (item.instrument_id, item.filled_quantity_contracts, item.transaction_cost_usd)
                for item in roll_fills
            ],
            [("YITH24", 2, D("34.000")), ("YITM24", 2, D("34.000"))],
        )
        self.assertEqual(result.daily[3].transaction_cost_usd, D("68.000"))

    # Mutation caught: opening a replacement leg before every retiring leg closes.
    def test_roll_closes_retiring_contracts_before_opening_replacements(self):
        frame = historical_frame()
        frame.loc[2:, "swap_ticker_2y"] = "YITM24"
        frame.loc[2:, "treasury_ticker_2y"] = "ZTM24"
        frame.loc[2:, "swap_price_2y"] = 125.0
        frame.loc[2:, "treasury_price_2y"] = 103.0
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=frame):
                result, _ = run_historical_backtest("roll-order", Path(directory))

        roll_orders = [
            item for item in result.orders
            if item.decision_id == "historical-2024-01-04-2y"
        ]
        self.assertEqual(
            [item.instrument_id for item in roll_orders],
            ["YITH24", "ZTH24", "YITM24", "ZTM24"],
        )

    # Mutation caught: calculating P&L from the desired, rather than filled, target.
    def test_held_position_pnl_matches_hand_calculation_and_exits_flat(self):
        frame = holding_frame()
        zero_costs = NaiveAssumptions(D("0"), D("0"), D("0"), D("0"), D("0"))
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=frame):
                result, _ = run_historical_backtest(
                    "historical-pnl", Path(directory), assumptions=zero_costs
                )

        self.assertEqual(result.daily[3].gross_pnl_usd, D("220.0"))
        self.assertEqual(result.daily[3].net_pnl_usd, D("220.0"))
        self.assertEqual(result.daily[3].equity_usd, D("1000220.0"))
        self.assertEqual(
            [fill.filled_quantity_contracts for fill in result.fills if fill.fill_time_utc.date().isoformat() == "2024-01-08"],
            [2, 1],
        )
        self.assertFalse(any(item.timestamp_utc.date().isoformat() == "2024-01-08" for item in result.positions))

    # Mutation caught: incorporating rate-spread diagnostics into historical
    # futures price P&L accounting.
    def test_rate_spread_diagnostics_do_not_change_price_pnl(self):
        zero_costs = NaiveAssumptions(D("0"), D("0"), D("0"), D("0"), D("0"))
        baseline_frame = holding_frame()
        rate_spread_frame = baseline_frame.assign(
            swap_spread_bps_2y=[10.0, 20.0, 30.0, 40.0, 50.0],
            swap_spread_bps_2y_z=[float("nan"), 0.7, 1.0, 1.0, 1.0],
            treasury_rate_proxy_bps_2y=[400.0] * 5,
        )
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=baseline_frame):
                baseline, _ = run_historical_backtest("historical-pnl-base", Path(directory), assumptions=zero_costs)
            with patch("backtesting.historical._load_historical_frame", return_value=rate_spread_frame):
                with_diagnostics, _ = run_historical_backtest(
                    "historical-pnl-rate-diagnostics", Path(directory), assumptions=zero_costs
                )

        self.assertEqual(baseline.daily[3].gross_pnl_usd, D("220.0"))
        self.assertEqual(baseline.daily[3].net_pnl_usd, D("220.0"))
        self.assertEqual(baseline.daily[3].equity_usd, D("1000220.0"))
        self.assertEqual(
            [(item.gross_pnl_usd, item.net_pnl_usd, item.equity_usd) for item in baseline.daily],
            [(item.gross_pnl_usd, item.net_pnl_usd, item.equity_usd) for item in with_diagnostics.daily],
        )
        self.assertEqual(baseline.summary, with_diagnostics.summary)

    # Mutation caught: silently treating a missing current mark for a held
    # contract as a valid zero-return observation.
    def test_held_position_missing_current_mark_is_recorded_in_manifest(self):
        frame = holding_frame()
        frame.loc[3, "swap_price_2y"] = float("nan")
        frame.loc[3, "swap_ticker_2y"] = ""
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=frame):
                result, _ = run_historical_backtest("missing-mark", Path(directory))

        self.assertIn(
            "2024-01-05:YITH24:current_mark",
            dict(result.manifest)["missing_input_locations"],
        )

    # Mutation caught: carrying pre-window equity or positions into a requested
    # historical window instead of starting that replay flat.
    def test_requested_window_starts_with_requested_initial_equity(self):
        initial_equity = D("987654.32")
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=holding_frame()):
                result, _ = run_historical_backtest(
                    "window-start-flat",
                    Path(directory),
                    start="2024-01-03",
                    end="2024-01-05",
                    initial_equity_usd=initial_equity,
                )

        self.assertEqual(result.daily[0].equity_usd, initial_equity)

    # Mutation caught: resolving an earlier held ticker from a later row that reuses it.
    def test_future_ticker_reuse_cannot_revise_earlier_strategy_outputs(self):
        frame = historical_frame()
        frame.loc[2, "risk_allowed"] = 0
        frame.loc[2, "risk_block_reason"] = "portfolio:net_dv01_limit"
        frame.loc[2, [
            "swap_futures_contracts_rounded_2y",
            "treasury_futures_contracts_rounded_2y",
        ]] = 0
        future = frame.iloc[[-1]].assign(date=pd.Timestamp("2024-01-08"))
        future["swap_ticker_2y"] = ""
        future["swap_ticker_5y"] = "YITH24"
        future["swap_price_5y"] = 100.0
        future["swap_dv01_per_contract_5y"] = 19.0
        future["swap_futures_contracts_rounded_5y"] = 0
        extended = pd.concat([frame, future], ignore_index=True)
        extended["swap_futures_contracts_rounded_5y"] = extended[
            "swap_futures_contracts_rounded_5y"
        ].fillna(0)
        with TemporaryDirectory() as directory:
            with patch("backtesting.historical._load_historical_frame", return_value=frame):
                baseline, _ = run_historical_backtest("prefix", Path(directory))
            with patch("backtesting.historical._load_historical_frame", return_value=extended):
                replayed, _ = run_historical_backtest("prefix", Path(directory))

        cutoff = pd.Timestamp("2024-01-05").date()
        self.assertEqual(
            [item for item in baseline.decisions if item.decision_time_utc.date() <= cutoff],
            [item for item in replayed.decisions if item.decision_time_utc.date() <= cutoff],
        )
        self.assertEqual(
            [item for item in baseline.orders if item.earliest_submission_utc.date() <= cutoff],
            [item for item in replayed.orders if item.earliest_submission_utc.date() <= cutoff],
        )


class HistoricalCliTests(unittest.TestCase):
    # Mutation caught: treating a monetary CLI override as a float or omitting
    # historical command-line options before dispatch.
    def test_cli_parses_decimal_costs_and_dispatches_once(self):
        args = parse_args([
            "--run-id", "cli-run",
            "--start", "2024-01-02",
            "--end", "2024-01-05",
            "--commission-usd-per-contract", "1.25",
        ])
        self.assertEqual(args.commission_usd_per_contract, D("1.25"))
        with patch("backtesting.__main__.run_historical_backtest") as run:
            run.return_value = (Mock(summary=()), Path("out/cli-run"))
            with redirect_stdout(StringIO()):
                self.assertEqual(main([
                    "--run-id", "cli-run",
                    "--start", "2024-01-02",
                    "--end", "2024-01-05",
                    "--output-root", "out/custom",
                    "--refresh-signals",
                    "--initial-equity", "987654.32",
                    "--bid-ask-half-spread-points", "0.125",
                    "--commission-usd-per-contract", "1.25",
                    "--slippage-points", "0.015",
                    "--financing-usd-per-contract-day", "0.10",
                    "--roll-usd-per-contract", "2.50",
                ]), 0)
        run.assert_called_once_with(
            "cli-run",
            Path("out/custom"),
            "2024-01-02",
            "2024-01-05",
            True,
            NaiveAssumptions(
                D("0.125"), D("1.25"), D("0.015"), D("0.10"), D("2.50")
            ),
            D("987654.32"),
        )

    # Mutation caught: accepting a negative, non-finite, or non-decimal cost.
    def test_cli_rejects_invalid_decimal_values(self):
        for value in ("-1", "NaN", "not-a-number"):
            with self.subTest(value=value), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    parse_args(["--commission-usd-per-contract", value])
            self.assertEqual(error.exception.code, 2)

    # Mutation caught: wiring --self-check to live historical-data refreshes.
    def test_self_check_is_offline_and_passes(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--self-check"]), 0)
        self.assertIn("[OK] backtesting self-check passed", output.getvalue())
