from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import signal_pipeline

from clean_data import clean_existing_derived_csvs, without_dv01_columns
from data_pipeline.historical_data.historical_data_builder import (
    build_cme_swap_data,
    build_ctd_treasury_futures_data,
    build_treasury_futures_data,
    equivalent_par_sofr_swap_rate_bps,
    extract_eris_swap_row,
    fetch_eris_settlement_text,
    parse_yahoo_chart,
    strategy_swap_prices,
    strategy_treasury_futures_prices,
)
from risk_pipeline import (
    build_risk_data,
    load_cme_swap_data,
    load_treasury_futures_data,
    merge_cme_dv01,
    merge_treasury_futures_data,
)
from signal_pipeline import build_proxy_position, build_signal_columns


def sample_selected_swaps() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "eris_swap_2y_ticker": ["YITH24", "YITM24"],
            "eris_swap_2y_price": [99.1, 99.2],
            "eris_swap_2y_return": [0.0, 0.001],
            "eris_swap_2y_dv01": [19.0, 19.1],
            "eris_swap_5y_ticker": ["YIWH24", "YIWM24"],
            "eris_swap_5y_price": [98.1, 98.2],
            "eris_swap_5y_return": [0.0, 0.001],
            "eris_swap_5y_dv01": [46.0, 46.1],
        }
    )


class ErisCacheTests(unittest.TestCase):
    def test_historical_missing_dates_are_not_requested_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            date = pd.Timestamp("2020-12-25")
            with patch(
                "data_pipeline.historical_data.historical_data_builder.CACHE_DIR",
                Path(directory),
            ), patch(
                "data_pipeline.historical_data.historical_data_builder.eris_settlement_url_candidates",
                return_value=[],
            ):
                self.assertIsNone(fetch_eris_settlement_text(date))

            marker = (
                Path(directory)
                / "eris_sofr_settlements_v3"
                / "Eris_Instruments_20201225_Settles.missing"
            )
            self.assertTrue(marker.exists())

            with patch(
                "data_pipeline.historical_data.historical_data_builder.CACHE_DIR",
                Path(directory),
            ), patch(
                "data_pipeline.historical_data.historical_data_builder.eris_settlement_url_candidates",
                side_effect=AssertionError("negative cache should skip the network"),
            ):
                self.assertIsNone(fetch_eris_settlement_text(date))


def sample_treasury_prices() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"]
            ),
            "ticker": ["ZT=F", "ZF=F", "ZT=F", "ZF=F"],
            "price": [102.0, 108.0, 102.1, 108.2],
        }
    )


class DerivedCsvTests(unittest.TestCase):
    def test_without_dv01_columns_removes_every_matching_column(self) -> None:
        source = pd.DataFrame(
            {
                "date": ["2024-01-02"],
                "target_dv01_2y": [100.0],
                "price": [99.0],
                "DV01_source": [19.0],
            }
        )

        output = without_dv01_columns(source)

        self.assertEqual(output.columns.tolist(), ["date", "price"])

    def test_cleanup_preserves_master_and_scrubs_other_csvs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            master_path = data_dir / "cme_swap_data.csv"
            treasury_master_path = data_dir / "treasury_futures_data.csv"
            other_path = data_dir / "risk_data.csv"
            pd.DataFrame(
                {
                    "date": ["2024-01-02"],
                    "ticker": ["YITH24"],
                    "price": [99.0],
                    "dv01": [19.0],
                }
            ).to_csv(master_path, index=False)
            pd.DataFrame(
                {
                    "date": ["2024-01-02"],
                    "ticker": ["ZT=F"],
                    "price": [102.0],
                    "dv01": [38.0],
                }
            ).to_csv(treasury_master_path, index=False)
            pd.DataFrame(
                {
                    "date": ["2024-01-02"],
                    "target_dv01_2y": [100.0],
                    "contracts": [5],
                }
            ).to_csv(other_path, index=False)

            master_paths = [master_path, treasury_master_path]
            cleaned = clean_existing_derived_csvs(data_dir, master_paths)

            self.assertEqual(cleaned, [other_path])
            self.assertEqual(
                pd.read_csv(master_path).columns.tolist(),
                ["date", "ticker", "price", "dv01"],
            )
            self.assertEqual(
                pd.read_csv(treasury_master_path).columns.tolist(),
                ["date", "ticker", "price", "dv01"],
            )
            self.assertEqual(
                pd.read_csv(other_path).columns.tolist(),
                ["date", "contracts"],
            )
            self.assertEqual(
                clean_existing_derived_csvs(data_dir, master_paths),
                [],
            )


class SignalCalendarTests(unittest.TestCase):
    @staticmethod
    def curve_inputs(source: pd.DataFrame) -> pd.DataFrame:
        output = source.copy()
        output["dgs3"] = output["dgs2"]
        output["dgs7"] = output["dgs5"]
        output["eris_swap_2y_maturity_date"] = output["date"] + pd.to_timedelta(731, unit="D")
        output["eris_swap_5y_maturity_date"] = output["date"] + pd.to_timedelta(1827, unit="D")
        return output

    def test_daily_signal_does_not_require_treasury_futures_marks(self) -> None:
        source = self.curve_inputs(pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
                "eris_swap_2y_price": [100.0, 100.1, 100.2],
                "eris_swap_5y_price": [101.0, 101.1, 101.2],
                "treasury_futures_2y_price": [102.0, 102.1, 102.2],
                "treasury_futures_5y_price": [108.0, float("nan"), 108.2],
                "eris_swap_2y_equivalent_par_rate_bps": [410.0, 411.0, 412.0],
                "eris_swap_5y_equivalent_par_rate_bps": [420.0, 421.0, 422.0],
                "dgs2": [4.0, 4.0, 4.0],
                "dgs5": [4.1, 4.1, 4.1],
            }
        ))

        output = build_signal_columns(source)

        self.assertEqual(
            output["date"].tolist(),
            [
                pd.Timestamp("2024-01-02"),
                pd.Timestamp("2024-01-03"),
                pd.Timestamp("2024-01-04"),
            ],
        )

    # Mutation caught: routing active positions through Treasury-futures price
    # residuals rather than the equivalent-par-rate Treasury spread.
    def test_rate_spreads_are_the_active_proxy_signal_source(self) -> None:
        source = self.curve_inputs(pd.DataFrame(
            {
                "date": pd.date_range("2024-01-02", periods=4, freq="B"),
                "eris_swap_2y_price": [100.0, 101.0, 102.0, 90.0],
                "eris_swap_5y_price": [105.0, 106.0, 107.0, 108.0],
                "treasury_futures_2y_price": [100.0, 101.0, 102.0, 103.0],
                "treasury_futures_5y_price": [110.0, 111.0, 112.0, 113.0],
                "eris_swap_2y_equivalent_par_rate_bps": [410.0, 430.0, 450.0, 470.0],
                "eris_swap_5y_equivalent_par_rate_bps": [320.0, 340.0, 360.0, 380.0],
                "dgs2": [4.0, 4.0, 4.0, 4.0],
                "dgs5": [3.0, 3.0, 3.0, 3.0],
            }
        ))

        with (
            patch.object(signal_pipeline, "ROLLING_WINDOW", 3),
            patch.object(signal_pipeline, "MIN_PERIODS", 2),
            patch.object(signal_pipeline, "Z_ENTRY", 0.5),
        ):
            output = build_signal_columns(source)
            self.assertEqual(output["treasury_rate_proxy_bps_2y"].tolist(), [400.0, 400.0, 400.0, 400.0])
            self.assertEqual(output["swap_spread_bps_2y"].tolist(), [10.0, 30.0, 50.0, 70.0])
            self.assertEqual(
                output["proxy_position_2y"].tolist(),
                build_proxy_position(output["swap_spread_bps_2y_z"]).tolist(),
            )
            self.assertNotEqual(
                output["proxy_position_2y"].tolist(),
                build_proxy_position(output["eris_swap_2y_price_residual_z"]).tolist(),
            )
            self.assertIn("eris_swap_2y_price_residual_vs_treasury", output.columns)
            self.assertIn("eris_swap_2y_price_residual_z", output.columns)

    # Mutation caught: substituting the former price residual when a rate input
    # is unavailable instead of resetting the rate-spread position flat.
    def test_missing_equivalent_par_rate_fails_closed_per_maturity(self) -> None:
        source = self.curve_inputs(pd.DataFrame(
            {
                "date": pd.date_range("2024-01-02", periods=4, freq="B"),
                "eris_swap_2y_price": [100.0, 101.0, 102.0, 103.0],
                "eris_swap_5y_price": [105.0, 106.0, 107.0, 108.0],
                "treasury_futures_2y_price": [100.0, 101.0, 102.0, 103.0],
                "treasury_futures_5y_price": [110.0, 111.0, 112.0, 113.0],
                "eris_swap_2y_equivalent_par_rate_bps": [410.0, float("nan"), 450.0, 470.0],
                "eris_swap_5y_equivalent_par_rate_bps": [320.0, 340.0, 360.0, 380.0],
                "dgs2": [4.0, 4.0, 4.0, 4.0],
                "dgs5": [3.0, 3.0, 3.0, 3.0],
            }
        ))

        with (
            patch.object(signal_pipeline, "ROLLING_WINDOW", 3),
            patch.object(signal_pipeline, "MIN_PERIODS", 2),
        ):
            output = build_signal_columns(source)

        self.assertTrue(pd.isna(output.loc[1, "swap_spread_bps_2y"]))
        self.assertTrue(pd.isna(output.loc[1, "swap_spread_bps_2y_z"]))
        self.assertEqual(output.loc[1, "proxy_position_2y"], 0)
        self.assertFalse(pd.isna(output.loc[1, "swap_spread_bps_5y"]))


class ErisEquivalentParRateTests(unittest.TestCase):
    def test_technical_documentation_describes_rate_based_signal(self) -> None:
        text = (
            Path(__file__).resolve().parents[2] / "docs" / "TECHNICAL_DOCUMENTATION.md"
        ).read_text(encoding="utf-8")

        self.assertIn("equivalent_par_rate_bps", text)
        self.assertIn("swap_spread_bps", text)
        self.assertIn("maturity-matched Treasury CMT curve", text)
        self.assertIn("not a CTD", text)
        self.assertIn("coupon P&L is not added separately", text)

    def test_equivalent_par_rate_uses_documented_units_and_sign(self) -> None:
        """Catches a reversed price sign or an incorrect percent/basis-point conversion."""
        self.assertEqual(
            equivalent_par_sofr_swap_rate_bps(100.0, 4.50, 0.0, 0.0, 19.0),
            450.0,
        )
        self.assertEqual(
            equivalent_par_sofr_swap_rate_bps(100.019, 4.50, 0.0, 0.0, 19.0),
            449.0,
        )
        self.assertEqual(
            equivalent_par_sofr_swap_rate_bps(99.981, 4.50, 0.0, 0.0, 19.0),
            451.0,
        )

    def test_equivalent_par_rate_rejects_invalid_inputs(self) -> None:
        """Catches invalid market values reaching the conversion as a numeric rate."""
        for values in (
            (None, 4.5, 0.0, 0.0, 19.0),
            (100.0, None, 0.0, 0.0, 19.0),
            (100.0, 4.5, None, 0.0, 19.0),
            (100.0, 4.5, 0.0, None, 19.0),
            (100.0, 4.5, 0.0, 0.0, None),
            (0.0, 4.5, 0.0, 0.0, 19.0),
            (100.0, 4.5, 0.0, 0.0, 0.0),
            (float("nan"), 4.5, 0.0, 0.0, 19.0),
            (100.0, 4.5, float("inf"), 0.0, 19.0),
        ):
            self.assertIsNone(equivalent_par_sofr_swap_rate_bps(*values))


class CmeMasterTests(unittest.TestCase):
    def test_active_contract_extraction_preserves_full_ticker(self) -> None:
        """Catches dropping or reading the conversion inputs from a non-selected contract."""
        settlements = pd.DataFrame(
            {
                "Symbol": ["YITH24", "YITM24"],
                "ExchangeSymbol (EX005)": ["YIT", "YIT"],
                "FinalSettlementPrice": [99.1, 99.2],
                "EvaluationDate": ["01/02/2024", "01/02/2024"],
                "LastTradeDate": ["03/18/2024", "06/17/2024"],
                "FloatingIndex": ["SOFR", "SOFR"],
                "DV01": [19.0, 20.0],
                "Coupon (%)": [4.50, 4.60],
                "PastFxdFltPmts (B)": [0.0, 0.0],
                "ErisPAI (C)": [0.0, 0.0],
                "PV01": [19.0, 20.0],
                "EffectiveDate": ["12/20/2023", "03/20/2024"],
                "MaturityDate": ["12/20/2025", "03/20/2026"],
            }
        )

        output = extract_eris_swap_row(
            settlements,
            pd.Timestamp("2024-01-02"),
            active_contracts={},
        )

        self.assertEqual(output["eris_swap_2y_ticker"], "YITH24")
        self.assertEqual(output["eris_swap_2y_price"], 99.1)
        self.assertEqual(output["eris_swap_2y_dv01"], 19.0)
        self.assertEqual(output["eris_swap_2y_fixed_coupon_pct"], 4.50)
        self.assertEqual(output["eris_swap_2y_b_usd"], 0.0)
        self.assertEqual(output["eris_swap_2y_c_usd"], 0.0)
        self.assertEqual(output["eris_swap_2y_pv01_usd_per_bp"], 19.0)
        self.assertEqual(output["eris_swap_2y_effective_date"], "12/20/2023")
        self.assertEqual(output["eris_swap_2y_maturity_date"], "12/20/2025")
        self.assertEqual(output["eris_swap_2y_last_trade_date"], "03/18/2024")
        self.assertEqual(
            output["eris_swap_2y_equivalent_par_rate_bps"],
            equivalent_par_sofr_swap_rate_bps(99.1, 4.50, 0.0, 0.0, 19.0),
        )

    def test_active_contract_extraction_omits_rate_when_pv01_is_zero(self) -> None:
        """Catches an invalid PV01 producing a rate while hiding the usable selected quote."""
        settlements = pd.DataFrame(
            {
                "Symbol": ["YITH24"],
                "ExchangeSymbol (EX005)": ["YIT"],
                "FinalSettlementPrice": [99.1],
                "EvaluationDate": ["01/02/2024"],
                "LastTradeDate": ["03/18/2024"],
                "FloatingIndex": ["SOFR"],
                "DV01": [19.0],
                "Coupon (%)": [4.50],
                "PastFxdFltPmts (B)": [0.0],
                "ErisPAI (C)": [0.0],
                "PV01": [0.0],
            }
        )

        output = extract_eris_swap_row(settlements, pd.Timestamp("2024-01-02"), active_contracts={})

        self.assertEqual(output["eris_swap_2y_ticker"], "YITH24")
        self.assertEqual(output["eris_swap_2y_price"], 99.1)
        self.assertNotIn("eris_swap_2y_equivalent_par_rate_bps", output)

    def test_active_contract_extraction_rolls_shared_state(self) -> None:
        active_contracts: dict[str, str] = {}
        first_day = pd.DataFrame(
            {
                "Symbol": ["YITH24", "YITM24"],
                "ExchangeSymbol (EX005)": ["YIT", "YIT"],
                "FinalSettlementPrice": [99.1, 98.0],
                "EvaluationDate": ["01/02/2024", "01/02/2024"],
                "LastTradeDate": ["03/18/2024", "06/17/2024"],
                "FloatingIndex": ["SOFR", "SOFR"],
                "DV01": [19.0, 20.5],
            }
        )
        second_day = first_day.copy()
        second_day["EvaluationDate"] = "01/03/2024"
        second_day["DV01"] = [10.0, 19.2]

        first = extract_eris_swap_row(
            first_day,
            pd.Timestamp("2024-01-02"),
            active_contracts=active_contracts,
        )
        second = extract_eris_swap_row(
            second_day,
            pd.Timestamp("2024-01-03"),
            active_contracts=active_contracts,
        )

        self.assertEqual(first["eris_swap_2y_ticker"], "YITH24")
        self.assertEqual(second["eris_swap_2y_ticker"], "YITM24")
        self.assertEqual(active_contracts["2Y"], "YITM24")

    def test_builds_sorted_four_column_master(self) -> None:
        output = build_cme_swap_data(sample_selected_swaps())

        expected = pd.DataFrame(
            {
                "date": pd.to_datetime(
                    ["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"]
                ),
                "ticker": ["YITH24", "YIWH24", "YITM24", "YIWM24"],
                "price": [99.1, 98.1, 99.2, 98.2],
                "dv01": [19.0, 46.0, 19.1, 46.1],
            }
        )

        pd.testing.assert_frame_equal(output, expected)

    def test_strategy_prices_exclude_ticker_and_dv01(self) -> None:
        output = strategy_swap_prices(sample_selected_swaps())

        self.assertFalse(any("dv01" in column.lower() for column in output))
        self.assertFalse(any(column.endswith("_ticker") for column in output))
        self.assertEqual(
            output.columns.tolist(),
            [
                "date",
                "eris_swap_2y_price",
                "eris_swap_2y_return",
                "eris_swap_5y_price",
                "eris_swap_5y_return",
            ],
        )
        self.assertEqual(output["eris_swap_2y_price"].tolist(), [99.1, 99.1])
        self.assertEqual(output["eris_swap_2y_return"].tolist(), [0.0, 0.0])


class TreasuryMasterTests(unittest.TestCase):
    def test_parses_yahoo_chart_into_long_price_rows(self) -> None:
        text = """{
          "chart": {"result": [{
            "timestamp": [1704196800, 1704283200],
            "indicators": {"quote": [{"close": [102.25, null]}]}
          }], "error": null}
        }"""

        output = parse_yahoo_chart(text, "ZT=F")

        self.assertEqual(output.columns.tolist(), ["date", "ticker", "price"])
        self.assertEqual(output["ticker"].tolist(), ["ZT=F"])
        self.assertEqual(output["price"].tolist(), [102.25])

    def test_builds_treasury_master_from_cme_hedge_ratios(self) -> None:
        output = build_treasury_futures_data(
            sample_treasury_prices(),
            build_cme_swap_data(sample_selected_swaps()),
        )

        self.assertEqual(output.columns.tolist(), ["date", "ticker", "price", "dv01"])
        self.assertEqual(output["dv01"].tolist(), [46.0, 38.0, 46.1, 38.2])
        self.assertFalse(output.duplicated(["date", "ticker"]).any())

    def test_strategy_treasury_prices_are_wide_and_have_no_dv01(self) -> None:
        master = build_treasury_futures_data(
            sample_treasury_prices(),
            build_cme_swap_data(sample_selected_swaps()),
        )

        output = strategy_treasury_futures_prices(master)

        self.assertEqual(
            output.columns.tolist(),
            [
                "date",
                "treasury_futures_2y_price",
                "treasury_futures_2y_return",
                "treasury_futures_5y_price",
                "treasury_futures_5y_return",
            ],
        )
        self.assertFalse(any("dv01" in column.lower() for column in output))
        self.assertFalse(any(column.endswith("ticker") for column in output))

    def test_builds_contract_treasury_master_from_ctd_inputs(self) -> None:
        source = pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-02"],
                "ticker": ["ZTH24", "ZFH24"],
                "price": [102.0, 108.0],
                "ctd_cash_dv01_per_100k": [20.0, 40.0],
                "conversion_factor": [0.8, 0.8],
            }
        )

        output = build_ctd_treasury_futures_data(source)

        self.assertEqual(output.columns.tolist(), ["date", "ticker", "price", "dv01"])
        self.assertEqual(output["ticker"].tolist(), ["ZFH24", "ZTH24"])
        self.assertEqual(output["dv01"].tolist(), [50.0, 50.0])

    def test_ctd_master_rejects_nonpositive_conversion_factor(self) -> None:
        source = pd.DataFrame(
            {
                "date": ["2024-01-02"],
                "ticker": ["ZTH24"],
                "price": [102.0],
                "ctd_cash_dv01_per_100k": [20.0],
                "conversion_factor": [0.0],
            }
        )

        with self.assertRaisesRegex(RuntimeError, "CTD Treasury futures input"):
            build_ctd_treasury_futures_data(source)

    def test_ctd_master_requires_both_strategy_roots(self) -> None:
        source = pd.DataFrame(
            {
                "date": ["2024-01-02"],
                "ticker": ["ZTH24"],
                "price": [102.0],
                "ctd_cash_dv01_per_100k": [20.0],
                "conversion_factor": [0.8],
            }
        )

        with self.assertRaisesRegex(RuntimeError, "requires ZT and ZF"):
            build_ctd_treasury_futures_data(source)

    def test_ctd_master_rejects_overlapping_same_root_contracts(self) -> None:
        source = pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-02", "2024-01-02"],
                "ticker": ["ZTH24", "ZTM24", "ZFH24"],
                "price": [102.0, 101.9, 108.0],
                "ctd_cash_dv01_per_100k": [20.0, 20.1, 40.0],
                "conversion_factor": [0.8, 0.81, 0.8],
            }
        )

        with self.assertRaisesRegex(RuntimeError, "Multiple 2Y"):
            build_ctd_treasury_futures_data(source)

    def test_full_ticker_treasury_roll_is_back_adjusted(self) -> None:
        master = pd.DataFrame(
            {
                "date": pd.to_datetime(
                    ["2024-03-28", "2024-03-28", "2024-03-29", "2024-03-29"]
                ),
                "ticker": ["ZFH24", "ZTH24", "ZFM24", "ZTM24"],
                "price": [108.0, 102.0, 106.0, 101.0],
                "dv01": [50.0, 50.0, 50.0, 50.0],
            }
        )

        output = strategy_treasury_futures_prices(master)

        self.assertEqual(output["treasury_futures_2y_price"].tolist(), [102.0, 102.0])
        self.assertEqual(output["treasury_futures_2y_return"].iloc[1], 0.0)
        self.assertEqual(output["treasury_futures_5y_price"].tolist(), [108.0, 108.0])

class RiskMasterTests(unittest.TestCase):
    def test_loader_rejects_duplicate_date_ticker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cme_swap_data.csv"
            pd.DataFrame(
                {
                    "date": ["2024-01-02", "2024-01-02"],
                    "ticker": ["YITH24", "YITH24"],
                    "price": [99.0, 99.1],
                    "dv01": [19.0, 19.1],
                }
            ).to_csv(path, index=False)

            with self.assertRaisesRegex(RuntimeError, "duplicate date/ticker"):
                load_cme_swap_data(path)

    def test_loader_rejects_nonpositive_price(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cme_swap_data.csv"
            pd.DataFrame(
                {
                    "date": ["2024-01-02"],
                    "ticker": ["YITH24"],
                    "price": [0.0],
                    "dv01": [19.0],
                }
            ).to_csv(path, index=False)

            with self.assertRaisesRegex(RuntimeError, "missing or invalid"):
                load_cme_swap_data(path)

    def test_exact_date_merge_does_not_forward_fill(self) -> None:
        signals = pd.DataFrame(
            {"date": ["2024-01-02", "2024-01-03", "2024-01-04"]}
        )
        master = pd.DataFrame(
            {
                "date": pd.to_datetime(
                    ["2024-01-02", "2024-01-02", "2024-01-04", "2024-01-04"]
                ),
                "ticker": ["YITH24", "YIWH24", "YITM24", "YIWM24"],
                "price": [99.0, 98.0, 99.1, 98.1],
                "dv01": [19.0, 46.0, 20.0, 47.0],
            }
        )

        output = merge_cme_dv01(signals, master)

        self.assertEqual(output.loc[0, "swap_dv01_per_contract_2y"], 19.0)
        self.assertTrue(pd.isna(output.loc[1, "swap_dv01_per_contract_2y"]))
        self.assertEqual(output.loc[2, "swap_dv01_per_contract_2y"], 20.0)
        self.assertEqual(output.loc[0, "swap_dv01_per_contract_5y"], 46.0)

    def test_treasury_loader_and_exact_date_merge(self) -> None:
        master = pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-04"],
                "ticker": ["ZT=F", "ZT=F"],
                "price": [102.0, 102.1],
                "dv01": [38.0, 38.1],
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "treasury_futures_data.csv"
            master.to_csv(path, index=False)
            loaded = load_treasury_futures_data(path)

        signals = pd.DataFrame(
            {"date": ["2024-01-02", "2024-01-03", "2024-01-04"]}
        )
        output = merge_treasury_futures_data(signals, loaded)

        self.assertEqual(output.loc[0, "treasury_dv01_per_contract_2y"], 38.0)
        self.assertTrue(pd.isna(output.loc[1, "treasury_dv01_per_contract_2y"]))
        self.assertEqual(output.loc[2, "treasury_dv01_per_contract_2y"], 38.1)

    def test_build_risk_uses_master_and_returns_no_dv01_columns(self) -> None:
        signals = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02"]),
                "proxy_position_2y": [1],
                "eris_swap_2y_price_residual_vs_treasury": [1.0],
                "eris_swap_2y_price_residual_z": [2.0],
            }
        )
        master = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02"]),
                "ticker": ["YITH24"],
                "price": [99.0],
                "dv01": [20.0],
            }
        )
        treasury_master = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02"]),
                "ticker": ["ZT=F"],
                "price": [102.0],
                "dv01": [40.0],
            }
        )

        with (
            patch("risk_pipeline.load_signal_or_build", return_value=signals),
            patch("risk_pipeline.load_cme_swap_data", return_value=master),
            patch("risk_pipeline.load_treasury_futures_data", return_value=treasury_master),
        ):
            output = build_risk_data(save=False)

        self.assertEqual(output.loc[0, "swap_futures_contracts_rounded_2y"], 150)
        self.assertEqual(output.loc[0, "treasury_futures_contracts_rounded_2y"], -75)
        self.assertFalse(any("dv01" in column.lower() for column in output))
        self.assertEqual(output.loc[0, "risk_allowed"], 1)

    # Mutation caught: sizing position strength or volatility from the legacy
    # residual columns instead of the active rate-spread inputs.
    def test_build_risk_uses_rate_spread_strength_and_volatility(self) -> None:
        signals = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-02", periods=9),
                "proxy_position_2y": [1] * 9,
                "swap_spread_bps_2y": [0.0] * 8 + [10.0],
                "swap_spread_bps_2y_z": [2.0] * 9,
                "eris_swap_2y_price_residual_vs_treasury": [999.0] * 9,
                "eris_swap_2y_price_residual_z": [0.0] * 9,
            }
        )
        master = pd.DataFrame(
            {
                "date": signals["date"],
                "ticker": ["YITH24"] * 9,
                "price": [99.0] * 9,
                "dv01": [20.0] * 9,
            }
        )
        treasury_master = pd.DataFrame(
            {
                "date": signals["date"],
                "ticker": ["ZT=F"] * 9,
                "price": [102.0] * 9,
                "dv01": [40.0] * 9,
            }
        )

        with (
            patch("risk_pipeline.load_signal_or_build", return_value=signals),
            patch("risk_pipeline.load_cme_swap_data", return_value=master),
            patch("risk_pipeline.load_treasury_futures_data", return_value=treasury_master),
            patch("risk_pipeline.DV01_VOL_LOOKBACK", 4),
            patch("risk_pipeline.DV01_VOL_MIN_PERIODS", 2),
        ):
            output = build_risk_data(save=False)

        self.assertEqual(output.loc[8, "swap_futures_contracts_rounded_2y"], 38)
        self.assertEqual(output.loc[8, "treasury_futures_contracts_rounded_2y"], -19)

    # Mutation caught: allowing a legacy residual-based signal cache to reach
    # risk sizing when rate-spread columns prove it was not rebuilt.
    def test_legacy_cached_positions_are_rejected_before_risk_sizing(self) -> None:
        legacy_signals = pd.DataFrame(
            {
                "date": ["2024-01-02"],
                "proxy_position_2y": [1],
                "eris_swap_2y_price_residual_vs_treasury": [999.0],
                "eris_swap_2y_price_residual_z": [2.0],
            }
        )
        master = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02"]),
                "ticker": ["YITH24"],
                "price": [99.0],
                "dv01": [20.0],
            }
        )
        treasury_master = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02"]),
                "ticker": ["ZT=F"],
                "price": [102.0],
                "dv01": [40.0],
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            signal_path = Path(directory) / "signal_data.csv"
            legacy_signals.to_csv(signal_path, index=False)
            with (
                patch("risk_pipeline.SIGNAL_DATA_FILE", signal_path),
                patch("risk_pipeline.load_cme_swap_data", return_value=master),
                patch("risk_pipeline.load_treasury_futures_data", return_value=treasury_master),
            ):
                with self.assertRaisesRegex(RuntimeError, "refresh-signals"):
                    build_risk_data(save=False)

    def test_active_risk_is_blocked_for_missing_or_nonpositive_dv01(self) -> None:
        signals = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "proxy_position_2y": [1, 1],
                "eris_swap_2y_price_residual_vs_treasury": [1.0, 1.0],
                "eris_swap_2y_price_residual_z": [2.0, 2.0],
            }
        )
        master = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02"]),
                "ticker": ["YITH24"],
                "price": [99.0],
                "dv01": [0.0],
            }
        )
        treasury_master = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "ticker": ["ZT=F", "ZT=F"],
                "price": [102.0, 102.1],
                "dv01": [38.0, 38.0],
            }
        )

        with (
            patch("risk_pipeline.load_signal_or_build", return_value=signals),
            patch("risk_pipeline.load_cme_swap_data", return_value=master),
            patch("risk_pipeline.load_treasury_futures_data", return_value=treasury_master),
        ):
            output = build_risk_data(save=False)

        self.assertEqual(output["swap_futures_contracts_rounded_2y"].tolist(), [0, 0])
        self.assertEqual(output["risk_allowed"].tolist(), [0, 0])
        self.assertTrue(
            output["risk_block_reason"].str.contains("missing_actual_swap_dv01").all()
        )

    def test_missing_treasury_dv01_blocks_both_legs(self) -> None:
        signals = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02"]),
                "proxy_position_2y": [1],
                "eris_swap_2y_price_residual_vs_treasury": [1.0],
                "eris_swap_2y_price_residual_z": [2.0],
            }
        )
        swap_master = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02"]),
                "ticker": ["YITH24"],
                "price": [99.0],
                "dv01": [19.0],
            }
        )
        treasury_master = pd.DataFrame(
            columns=["date", "ticker", "price", "dv01"]
        )

        with (
                patch("risk_pipeline.load_signal_or_build", return_value=signals),
                patch("risk_pipeline.load_cme_swap_data", return_value=swap_master),
                patch("risk_pipeline.load_treasury_futures_data", return_value=treasury_master),
        ):
            output = build_risk_data(save=False)

        self.assertEqual(output.loc[0, "swap_futures_contracts_rounded_2y"], 0)
        self.assertEqual(output.loc[0, "treasury_futures_contracts_rounded_2y"], 0)
        self.assertIn("missing_treasury_dv01_proxy", output.loc[0, "risk_block_reason"])


if __name__ == "__main__":
    unittest.main()
