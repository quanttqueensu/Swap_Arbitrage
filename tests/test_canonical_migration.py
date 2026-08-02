from __future__ import annotations

import csv
import hashlib
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from data_pipeline.canonicalize import (
    CanonicalizationError,
    FuturesCanonicalization,
    SourceTiming,
    canonicalize_daily_market,
    canonicalize_futures,
    canonicalize_rates,
)
from data_pipeline.contracts import SCHEMAS, validate_csv
from data_pipeline.manifests import FileManifest, manifest_digest, profile_file, write_input_manifest


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
        swaps = self.fixture("cme_swap_data.csv", ["date", "ticker", "price", "dv01"], [["2026-08-01", "YITU26", "99.25", "39.8"]])
        treasuries = self.fixture("treasury_futures_data.csv", ["date", "ticker", "price", "dv01"], [["2026-08-01", "ZT=F", "108.5", "79.6"]])

        result = canonicalize_futures(swaps, treasuries)
        self.assertIsInstance(result, FuturesCanonicalization)
        settlements, risk = result.settlements_by_year, result.risk_by_year

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
            "ERIS": (SourceTiming(date(2026, 1, 1), date(2026, 12, 31), time(21, tzinfo=timezone.utc), timedelta(minutes=1), "ERIS", "exact"),),
            "YAHOO": (SourceTiming(date(2026, 1, 1), date(2026, 12, 31), time(21, tzinfo=timezone.utc), timedelta(minutes=1), "YAHOO", "proxy", "continuous futures proxy"),),
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

        swaps = self.fixture("bad-swap.csv", ["date", "ticker", "price", "dv01"], [["2026-08-01", "YITU26", "0", "39.8"]])
        treasuries = self.fixture("ok-treasury.csv", ["date", "ticker", "price", "dv01"], [["2026-08-01", "ZT=F", "108.5", "79.6"]])
        with self.assertRaises(CanonicalizationError):
            canonicalize_futures(swaps, treasuries)

        prices = self.fixture("bad-prices.csv", ["date", "eris_swap_2y_price", "eris_swap_2y_return", "eris_swap_5y_price", "eris_swap_5y_return"], [["2026-08-01", "99.25", "0", "98.5", "0"]])
        with self.assertRaises(CanonicalizationError):
            canonicalize_daily_market(prices, prices, {})

    def test_timing_rules_are_utc_effective_dated_and_fail_closed_for_gaps_or_overlaps(self) -> None:
        with self.assertRaises(CanonicalizationError):
            SourceTiming(date(2026, 1, 2), date(2026, 1, 1), time(21, tzinfo=timezone.utc), timedelta(), "ERIS", "exact")
        with self.assertRaises(CanonicalizationError):
            SourceTiming(date(2026, 1, 1), date(2026, 1, 1), time(21, tzinfo=timezone.utc), timedelta(seconds=-1), "ERIS", "exact")
        with self.assertRaises(CanonicalizationError):
            SourceTiming(date(2026, 1, 1), date(2026, 1, 1), time(21), timedelta(), "ERIS", "exact")
        with self.assertRaises(CanonicalizationError):
            SourceTiming(date(2026, 1, 1), date(2026, 1, 1), time(21, tzinfo=timezone(timedelta(hours=-4))), timedelta(), "ERIS", "exact")
        with self.assertRaises(CanonicalizationError):
            SourceTiming(date(2026, 1, 1), date(2026, 1, 1), datetime(2026, 1, 1, 21, tzinfo=timezone.utc), timedelta(), "ERIS", "exact")
        with self.assertRaises(CanonicalizationError):
            SourceTiming(date(2026, 1, 1), date(2026, 1, 1), time(21, tzinfo=timezone.utc), 1, "ERIS", "exact")

        swaps = self.fixture("dated-swaps.csv", ["date", "eris_swap_2y_price", "eris_swap_2y_return", "eris_swap_5y_price", "eris_swap_5y_return"], [["2026-08-01", "99.25", "0", "98.5", "0"]])
        treasuries = self.fixture("dated-treasuries.csv", ["date", "treasury_futures_2y_price", "treasury_futures_2y_return", "treasury_futures_5y_price", "treasury_futures_5y_return"], [["2026-08-01", "108.5", "0", "110.25", "0"]])
        rule = SourceTiming(date(2026, 1, 1), date(2026, 7, 31), time(21, tzinfo=timezone.utc), timedelta(), "ERIS", "exact")
        yahoo = SourceTiming(date(2026, 1, 1), date(2026, 12, 31), time(21, tzinfo=timezone.utc), timedelta(), "YAHOO", "proxy", "continuous futures proxy")
        with self.assertRaises(CanonicalizationError):
            canonicalize_daily_market(swaps, treasuries, {"ERIS": (rule,), "YAHOO": (yahoo,)})
        overlap = SourceTiming(date(2026, 8, 1), date(2026, 12, 31), time(21, tzinfo=timezone.utc), timedelta(), "ERIS", "exact")
        with self.assertRaises(CanonicalizationError):
            canonicalize_daily_market(swaps, treasuries, {"ERIS": (overlap, overlap), "YAHOO": (yahoo,)})

    def test_rejects_noncanonical_dates_and_undocumented_eris_shorthand(self) -> None:
        for bad_date in ("20260801", "2026-W31-6"):
            with self.subTest(bad_date=bad_date):
                row = [bad_date, *("4" for _ in range(15))]
                with self.assertRaises(CanonicalizationError):
                    canonicalize_rates(self.fixture(f"{bad_date}.csv", RATE_HEADER, [row]))
        swaps = self.fixture("short-eris.csv", ["date", "ticker", "price", "dv01"], [["2026-08-01", "YIU26", "99.25", "39.8"]])
        treasuries = self.fixture("valid-treasury.csv", ["date", "ticker", "price", "dv01"], [["2026-08-01", "ZT=F", "108.5", "79.6"]])
        with self.assertRaises(CanonicalizationError):
            canonicalize_futures(swaps, treasuries)


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / ".git").mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_rows(self, name: str, schema_id: str, rows: list[list[str]]) -> Path:
        path = self.root / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([column.name for column in SCHEMAS[schema_id].columns])
            writer.writerows(rows)
        return path

    def test_profile_hashes_validated_bytes_and_derives_date_coverage(self) -> None:
        path = self.write_rows("rates.csv", "historical_rates", [["2026-08-01", "UST", "DGS2", "2Y", "410"]])

        manifest = profile_file(self.root, path, SCHEMAS["historical_rates"])

        self.assertEqual(manifest.sha256, "85452f8588bfc392267c3ffd46e9cb392f0d0df66f607d97ea2c59607fd8cde0")
        self.assertEqual(manifest.row_count, 1)
        self.assertEqual((manifest.start_time, manifest.end_time), ("2026-08-01", "2026-08-01"))
        self.assertEqual(manifest.schema_version, "1.0.0")
        self.assertEqual(manifest.path, "rates.csv")

    def test_profile_rejects_empty_or_unvalidated_input(self) -> None:
        empty = self.write_rows("empty.csv", "historical_rates", [])
        with self.assertRaisesRegex(ValueError, "empty"):
            profile_file(self.root, empty, SCHEMAS["historical_rates"])
        bad = self.root / "bad.csv"
        bad.write_text("wrong\nvalue\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "header"):
            profile_file(self.root, bad, SCHEMAS["historical_rates"])

    def test_digest_and_atomic_writer_sort_by_normalized_path(self) -> None:
        rows = [
            FileManifest("z\\rates.csv", "b" * 64, 2, "2026-08-02", "2026-08-03", "1.0.0"),
            FileManifest("a/rates.csv", "a" * 64, 1, "2026-08-01", "2026-08-01", "1.0.0"),
        ]
        output = self.root / "p24_inputs.csv"

        first = manifest_digest(rows)
        second = write_input_manifest(output, "p24-run", list(reversed(rows)))

        self.assertEqual(first, manifest_digest(list(reversed(rows))))
        self.assertEqual(second, first)
        self.assertFalse(output.with_name(f"{output.name}.tmp").exists())
        with output.open("r", encoding="utf-8", newline="") as handle:
            written = list(csv.DictReader(handle))
        self.assertEqual([row["path"] for row in written], ["a/rates.csv", "z/rates.csv"])
        self.assertEqual(validate_csv(SCHEMAS["run_inputs"], output), 2)

    def test_profile_uses_one_snapshot_when_file_changes_after_validation(self) -> None:
        path = self.write_rows("rates.csv", "historical_rates", [["2026-08-01", "UST", "DGS2", "2Y", "410"]])
        original_validate = validate_csv

        def mutate_after_validation(contract: object, candidate: Path) -> int:
            result = original_validate(contract, candidate)
            with candidate.open("a", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerow(["2026-08-02", "UST", "DGS2", "2Y", "411"])
            return result

        with patch("data_pipeline.manifests.validate_csv", side_effect=mutate_after_validation):
            with self.assertRaisesRegex(ValueError, "snapshot"):
                profile_file(self.root, path, SCHEMAS["historical_rates"])

    def test_profile_requires_exact_registered_contract_and_contained_nonsymlink_path(self) -> None:
        path = self.write_rows("rates.csv", "historical_rates", [["2026-08-01", "UST", "DGS2", "2Y", "410"]])
        with self.assertRaisesRegex(ValueError, "approved"):
            profile_file(self.root, path, replace(SCHEMAS["historical_rates"], version="1.0.0"))
        outside = Path(self.tempdir.name).parent / "outside-rates.csv"
        outside_root = outside.parent / ".git"
        outside_root.mkdir(exist_ok=True)
        self.addCleanup(lambda: outside_root.rmdir())
        outside.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        with self.assertRaisesRegex(ValueError, "repository"):
            profile_file(self.root, outside, SCHEMAS["historical_rates"])
        linked = self.root / "linked.csv"
        try:
            linked.symlink_to(path)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "symlink"):
            profile_file(self.root, linked, SCHEMAS["historical_rates"])

    def test_profile_rejects_validation_temp_replaced_with_different_valid_bytes(self) -> None:
        path = self.write_rows("rates.csv", "historical_rates", [["2026-08-01", "UST", "DGS2", "2Y", "410"]])
        replacement = self.write_rows("replacement.csv", "historical_rates", [["2026-08-02", "UST", "DGS2", "2Y", "411"]]).read_bytes()
        original_validate = validate_csv

        def replace_validation_temp(contract: object, candidate: Path) -> int:
            candidate.write_bytes(replacement)
            return original_validate(contract, candidate)

        with patch("data_pipeline.manifests.validate_csv", side_effect=replace_validation_temp):
            with self.assertRaisesRegex(ValueError, "snapshot"):
                profile_file(self.root, path, SCHEMAS["historical_rates"])

    def test_writer_requires_valid_run_id_and_preserves_destination_on_temp_validation_failure(self) -> None:
        output = self.root / "p24_inputs.csv"
        output.write_text("old destination", encoding="utf-8")
        rows = [FileManifest("rates.csv", "a" * 64, 1, "2026-08-01", "2026-08-01", "1.0.0")]
        with self.assertRaises(ValueError):
            write_input_manifest(output, "", rows)
        self.assertEqual(output.read_text(encoding="utf-8"), "old destination")
        with patch("data_pipeline.manifests.validate_csv", side_effect=ValueError("temp invalid")):
            with self.assertRaisesRegex(ValueError, "temp invalid"):
                write_input_manifest(output, "p24-run", rows)
        self.assertEqual(output.read_text(encoding="utf-8"), "old destination")
        self.assertEqual(list(output.parent.glob(f".{output.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
