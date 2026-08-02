from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import time, timedelta
from pathlib import Path

from data_pipeline.canonicalize import (
    CanonicalizationError,
    SourceTiming,
    canonicalize_daily_market,
    canonicalize_futures,
    canonicalize_rates,
)
from data_pipeline.contracts import SCHEMAS, validate_csv


RATE_HEADER = [
    "date", "dgs1mo", "dgs2mo", "dgs3mo", "dgs4mo", "dgs6mo", "dgs1", "dgs2",
    "dgs3", "dgs5", "dgs7", "dgs10", "dgs20", "dgs30", "sofr", "effr",
]


class CanonicalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def fixture(self, name: str, header: list[str], rows: list[list[str]]) -> Path:
        path = self.root / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def write_partition(self, name: str, rows: list[dict[str, str]], schema: str) -> Path:
        header = [column.name for column in SCHEMAS[schema].columns]
        return self.fixture(name, header, [[row[column] for column in header] for row in rows])

    def test_rates_preserve_provider_convert_percent_and_validate_partitions(self) -> None:
        path = self.fixture(
            "treasury_rates.csv",
            RATE_HEADER,
            [["2026-08-01", "4", "4", "4", "4", "4", "4", "4.10", "4", "4.25", "4", "4", "4", "4", "4.33", "4.34"]],
        )

        partitions = canonicalize_rates(path)

        self.assertEqual(partitions[2026][2], {
            "observation_date": "2026-08-01", "source": "UST", "series_id": "DGS2",
            "maturity": "2Y", "rate_bps": "410",
        })
        self.assertEqual(partitions[2026][-1], {
            "observation_date": "2026-08-01", "source": "UST", "series_id": "DGS5",
            "maturity": "5Y", "rate_bps": "425",
        })
        self.assertEqual({row["source"] for row in partitions[2026]}, {"UST", "NYFED"})
        self.assertEqual(validate_csv(SCHEMAS["historical_rates"], self.write_partition("rates.csv", partitions[2026], "historical_rates")), 4)

    def test_futures_build_expiry_aware_eris_ids_blank_settlement_dv01_and_proxy_risk(self) -> None:
        swaps = self.fixture("cme_swap_data.csv", ["date", "ticker", "price", "dv01"], [["2026-08-01", "YIU26", "99.25", "39.8"]])
        treasuries = self.fixture("treasury_futures_data.csv", ["date", "ticker", "price", "dv01"], [["2026-08-01", "ZT=F", "108.5", "79.6"]])

        settlements, risk = canonicalize_futures(swaps, treasuries)

        self.assertEqual(settlements[2026], [
            {"observation_date": "2026-08-01", "source": "ERIS", "instrument_id": "ERIS-YIT-202609", "settlement_price": "99.25", "dv01_usd_per_bp": ""},
            {"observation_date": "2026-08-01", "source": "YAHOO", "instrument_id": "YAHOO-CONTINUOUS-ZT", "settlement_price": "108.5", "dv01_usd_per_bp": ""},
        ])
        self.assertEqual(risk[2026], [
            {"observation_date": "2026-08-01", "instrument_id": "ERIS-YIT-202609", "dv01_usd_per_bp": "39.8", "rate_sensitivity_sign": "-1", "dv01_method": "eris_settlement_dv01"},
            {"observation_date": "2026-08-01", "instrument_id": "YAHOO-CONTINUOUS-ZT", "dv01_usd_per_bp": "79.6", "rate_sensitivity_sign": "-1", "dv01_method": "cme_fixed_ics_ratio_proxy"},
        ])
        self.assertEqual(validate_csv(SCHEMAS["historical_futures_settlements"], self.write_partition("settlements.csv", settlements[2026], "historical_futures_settlements")), 2)
        self.assertEqual(validate_csv(SCHEMAS["contract_risk"], self.write_partition("risk.csv", risk[2026], "contract_risk")), 2)

    def test_daily_market_applies_literal_timing_and_root_only_proxy_labels(self) -> None:
        swaps = self.fixture("swap_rates.csv", ["date", "eris_swap_2y_price", "eris_swap_2y_return", "eris_swap_5y_price", "eris_swap_5y_return"], [["2026-08-01", "99.25", "0", "98.5", "0"]])
        treasuries = self.fixture("treasury_futures.csv", ["date", "treasury_futures_2y_price", "treasury_futures_2y_return", "treasury_futures_5y_price", "treasury_futures_5y_return"], [["2026-08-01", "108.5", "0", "110.25", "0"]])
        timing = {
            "ERIS": SourceTiming(time(21), timedelta(minutes=1), "ERIS", "exact"),
            "YAHOO": SourceTiming(time(21), timedelta(minutes=1), "YAHOO", "proxy", "continuous futures proxy"),
        }

        partitions = canonicalize_daily_market(swaps, treasuries, timing)

        self.assertEqual(partitions[2026], [
            {"observation_date": "2026-08-01", "series_id": "", "instrument_id": "ERIS-YIT", "value": "99.25", "value_unit": "price_points", "source_observation_time_utc": "2026-08-01T21:00:00Z", "available_at_utc": "2026-08-01T21:01:00Z", "source": "ERIS", "classification": "exact", "proxy_label": ""},
            {"observation_date": "2026-08-01", "series_id": "", "instrument_id": "ERIS-YIW", "value": "98.5", "value_unit": "price_points", "source_observation_time_utc": "2026-08-01T21:00:00Z", "available_at_utc": "2026-08-01T21:01:00Z", "source": "ERIS", "classification": "exact", "proxy_label": ""},
            {"observation_date": "2026-08-01", "series_id": "", "instrument_id": "YAHOO-CONTINUOUS-ZF", "value": "110.25", "value_unit": "price_points", "source_observation_time_utc": "2026-08-01T21:00:00Z", "available_at_utc": "2026-08-01T21:01:00Z", "source": "YAHOO", "classification": "proxy", "proxy_label": "continuous futures proxy"},
            {"observation_date": "2026-08-01", "series_id": "", "instrument_id": "YAHOO-CONTINUOUS-ZT", "value": "108.5", "value_unit": "price_points", "source_observation_time_utc": "2026-08-01T21:00:00Z", "available_at_utc": "2026-08-01T21:01:00Z", "source": "YAHOO", "classification": "proxy", "proxy_label": "continuous futures proxy"},
        ])
        self.assertEqual(validate_csv(SCHEMAS["daily_market"], self.write_partition("market.csv", partitions[2026], "daily_market")), 4)

    def test_canonicalizers_fail_closed_for_bad_headers_duplicates_missing_and_invalid_numbers(self) -> None:
        cases = [
            ("unknown", [*RATE_HEADER, "extra"], [["2026-08-01", *(["4"] * 15), "x"]]),
            ("duplicate", RATE_HEADER, [["2026-08-01", *(["4"] * 15)], ["2026-08-01", *(["4"] * 15)]]),
            ("missing", RATE_HEADER, [["2026-08-01", "4", "4", "4", "4", "4", "4", "", "4", "4", "4", "4", "4", "4", "4", "4"]]),
            ("nonfinite", RATE_HEADER, [["2026-08-01", "4", "4", "4", "4", "4", "4", "NaN", "4", "4", "4", "4", "4", "4", "4", "4"]]),
            ("nonpositive", RATE_HEADER, [["2026-08-01", "4", "4", "4", "4", "4", "4", "0", "4", "4", "4", "4", "4", "4", "4", "4"]]),
        ]
        for name, header, rows in cases:
            with self.subTest(name=name):
                with self.assertRaises(CanonicalizationError):
                    canonicalize_rates(self.fixture(f"{name}.csv", header, rows))

        swaps = self.fixture("bad-swap.csv", ["date", "ticker", "price", "dv01"], [["2026-08-01", "YIU26", "0", "39.8"]])
        treasuries = self.fixture("ok-treasury.csv", ["date", "ticker", "price", "dv01"], [["2026-08-01", "ZT=F", "108.5", "79.6"]])
        with self.assertRaises(CanonicalizationError):
            canonicalize_futures(swaps, treasuries)

        prices = self.fixture("bad-prices.csv", ["date", "eris_swap_2y_price", "eris_swap_2y_return", "eris_swap_5y_price", "eris_swap_5y_return"], [["2026-08-01", "99.25", "0", "98.5", "0"]])
        with self.assertRaises(CanonicalizationError):
            canonicalize_daily_market(prices, prices, {})


if __name__ == "__main__":
    unittest.main()
