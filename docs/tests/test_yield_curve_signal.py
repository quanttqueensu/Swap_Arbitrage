from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import signal_pipeline
from data_pipeline.historical_data.historical_data_builder import (
    parse_treasury_yield_curve_xml,
    strategy_swap_prices,
)
from signals.yield_curve_signal import interpolate_treasury_yield_pct


def sample_raw(rows: int = 120) -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=rows, freq="B")
    frame = pd.DataFrame(
        {
            "date": dates,
            "dgs1": 4.20,
            "dgs2": 4.00,
            "dgs3": 3.90,
            "dgs5": 3.80,
            "dgs7": 3.75,
            "eris_swap_2y_price": np.linspace(99.0, 100.0, rows),
            "eris_swap_2y_equivalent_par_rate_bps": np.linspace(405.0, 415.0, rows),
            "eris_swap_5y_price": np.linspace(98.0, 99.0, rows),
            "eris_swap_5y_equivalent_par_rate_bps": np.linspace(385.0, 395.0, rows),
            "treasury_futures_2y_price": np.linspace(102.0, 103.0, rows),
            "treasury_futures_5y_price": np.linspace(110.0, 111.0, rows),
        }
    )
    frame["eris_swap_2y_maturity_date"] = frame["date"] + pd.to_timedelta(548, unit="D")
    frame["eris_swap_5y_maturity_date"] = frame["date"] + pd.to_timedelta(1644, unit="D")
    return frame


class YieldCurveSignalTests(unittest.TestCase):
    def test_parses_official_treasury_xml_namespaces(self) -> None:
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
              xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
          <entry><content><m:properties>
            <d:NEW_DATE>2024-01-02T00:00:00</d:NEW_DATE>
            <d:BC_1YEAR>4.80</d:BC_1YEAR>
            <d:BC_2YEAR>4.33</d:BC_2YEAR>
          </m:properties></content></entry>
        </feed>"""
        output = parse_treasury_yield_curve_xml(xml)
        self.assertEqual(output.loc[0, "date"], pd.Timestamp("2024-01-02"))
        self.assertEqual(output.loc[0, "dgs1"], 4.8)
        self.assertEqual(output.loc[0, "dgs2"], 4.33)

    def test_interpolates_nodes_and_rejects_extrapolation(self) -> None:
        row = pd.Series({"dgs1": 3.0, "dgs2": 4.0, "dgs3": 5.0})
        self.assertEqual(interpolate_treasury_yield_pct(row, 2.0), 4.0)
        self.assertEqual(interpolate_treasury_yield_pct(row, 1.5), 3.5)
        self.assertTrue(np.isnan(interpolate_treasury_yield_pct(row, 4.0)))

    def test_curve_mode_uses_contract_maturity_and_preserves_output_schema(self) -> None:
        raw = sample_raw()
        with patch.object(signal_pipeline, "YIELD_CURVE_CONSTRUCTION_SIGNAL", False):
            standard = signal_pipeline.build_signal_columns(raw)
        with patch.object(signal_pipeline, "YIELD_CURVE_CONSTRUCTION_SIGNAL", True):
            curve = signal_pipeline.build_signal_columns(raw)

        self.assertEqual(set(standard), set(curve))
        self.assertNotIn("eris_swap_2y_maturity_date", curve)
        matched_bps = float(curve.loc[0, "treasury_rate_proxy_bps_2y"])
        self.assertGreater(matched_bps, 400.0)
        self.assertLess(matched_bps, 420.0)
        self.assertNotEqual(
            matched_bps,
            float(standard.loc[0, "treasury_rate_proxy_bps_2y"]),
        )

    def test_curve_mode_fails_closed_without_maturity_dates(self) -> None:
        raw = sample_raw().drop(columns=["eris_swap_2y_maturity_date"])
        with patch.object(signal_pipeline, "YIELD_CURVE_CONSTRUCTION_SIGNAL", True):
            with self.assertRaisesRegex(RuntimeError, "maturity_date"):
                signal_pipeline.build_signal_columns(raw)

    def test_raw_swap_prices_retain_curve_construction_dates(self) -> None:
        raw = sample_raw(rows=2).assign(
            eris_swap_2y_ticker=["YITH24", "YITH24"],
            eris_swap_5y_ticker=["YIWH24", "YIWH24"],
            eris_swap_2y_return=[0.0, 0.01],
            eris_swap_5y_return=[0.0, 0.01],
        )
        output = strategy_swap_prices(raw)
        self.assertIn("eris_swap_2y_maturity_date", output)
        self.assertIn("eris_swap_5y_maturity_date", output)


if __name__ == "__main__":
    unittest.main()
