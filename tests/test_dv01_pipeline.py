from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data_io import clean_existing_derived_csvs, without_dv01_columns
from raw_price_data import (
    build_cme_swap_data,
    extract_eris_swap_row,
    strategy_swap_prices,
)


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


if __name__ == "__main__":
    unittest.main()
