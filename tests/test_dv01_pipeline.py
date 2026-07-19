from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from data_io import clean_existing_derived_csvs, without_dv01_columns
from raw_price_data import (
    build_cme_swap_data,
    extract_eris_swap_row,
    strategy_swap_prices,
)
from risk_data import build_risk_data, load_cme_swap_data, merge_cme_dv01


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
                    "target_dv01_2y": [100.0],
                    "contracts": [5],
                }
            ).to_csv(other_path, index=False)

            cleaned = clean_existing_derived_csvs(data_dir, master_path)

            self.assertEqual(cleaned, [other_path])
            self.assertEqual(
                pd.read_csv(master_path).columns.tolist(),
                ["date", "ticker", "price", "dv01"],
            )
            self.assertEqual(
                pd.read_csv(other_path).columns.tolist(),
                ["date", "contracts"],
            )


class CmeMasterTests(unittest.TestCase):
    def test_active_contract_extraction_preserves_full_ticker(self) -> None:
        settlements = pd.DataFrame(
            {
                "Symbol": ["YITH24", "YITM24"],
                "ExchangeSymbol (EX005)": ["YIT", "YIT"],
                "FinalSettlementPrice": [99.1, 99.2],
                "EvaluationDate": ["01/02/2024", "01/02/2024"],
                "LastTradeDate": ["03/18/2024", "06/17/2024"],
                "FloatingIndex": ["SOFR", "SOFR"],
                "DV01": [19.0, 20.0],
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

    def test_exact_date_merge_does_not_forward_fill(self) -> None:
        signals = pd.DataFrame(
            {"date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])}
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

        with (
            patch("risk_data.load_signal_or_build", return_value=signals),
            patch("risk_data.load_cme_swap_data", return_value=master),
        ):
            output = build_risk_data(save=False)

        self.assertEqual(output.loc[0, "swap_futures_contracts_rounded_2y"], 150)
        self.assertFalse(any("dv01" in column.lower() for column in output))
        self.assertEqual(output.loc[0, "risk_allowed"], 1)


if __name__ == "__main__":
    unittest.main()
