from __future__ import annotations

import csv
import hashlib
import json
import os
import socket
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
from data_pipeline.contracts import SCHEMAS, SchemaValidationError, validate_csv, validate_csv_bytes
from data_pipeline.manifests import FileManifest, manifest_digest, profile_file, write_input_manifest


RATE_HEADER = [
    "date", "dgs1mo", "dgs2mo", "dgs3mo", "dgs4mo", "dgs6mo", "dgs1", "dgs2",
    "dgs3", "dgs5", "dgs7", "dgs10", "dgs20", "dgs30", "sofr", "effr",
]


def _write_migration_fixture(repo: Path, relative: str, header: list[str], rows: list[list[str]]) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _populate_migration_repo(repo: Path) -> None:
    repo.mkdir()
    (repo / ".git").mkdir()
    _write_migration_fixture(repo, "data/treasury_rates.csv", RATE_HEADER, [
        ["2025-12-31", *(["3"] * 6), "3.10", "3", "3.25", "3", "3", "3", "3", "3.33", "3.34"],
        ["2026-08-01", *(["4"] * 6), "4.10", "4", "4.25", "4", "4", "4", "4", "4.33", "4.34"],
    ])
    _write_migration_fixture(repo, "data/cme_swap_data.csv", ["date", "ticker", "price", "dv01"], [
        ["2025-12-31", "YITZ25", "98.75", "38.8"],
        ["2026-08-01", "YITU26", "99.25", "39.8"],
    ])
    _write_migration_fixture(repo, "data/treasury_futures_data.csv", ["date", "ticker", "price", "dv01"], [
        ["2025-12-31", "ZT=F", "107.5", "78.6"],
        ["2026-08-01", "ZT=F", "108.5", "79.6"],
    ])
    _write_migration_fixture(repo, "data/swap_rates.csv", ["date", "eris_swap_2y_price", "eris_swap_2y_return", "eris_swap_5y_price", "eris_swap_5y_return"], [
        ["2025-12-31", "98.75", "0", "97.5", "0"],
        ["2026-08-01", "99.25", "0", "98.5", "0"],
    ])
    _write_migration_fixture(repo, "data/treasury_futures.csv", ["date", "treasury_futures_2y_price", "treasury_futures_2y_return", "treasury_futures_5y_price", "treasury_futures_5y_return"], [
        ["2025-12-31", "107.5", "0", "109.25", "0"],
        ["2026-08-01", "108.5", "0", "110.25", "0"],
    ])


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

    def test_rates_emit_only_present_consumed_series_without_zero_or_proxy(self) -> None:
        path = self.fixture(
            "rates-with-absences.csv",
            RATE_HEADER,
            [["2026-08-01", *(["4"] * 6), "", "4", "4.25", "4", "4", "4", "4", "", "4.34"]],
        )
        partitions = canonicalize_rates(path)
        self.assertEqual(partitions[2026], [
            {"observation_date": "2026-08-01", "source": "NYFED", "series_id": "EFFR", "maturity": "ON", "rate_bps": "434"},
            {"observation_date": "2026-08-01", "source": "UST", "series_id": "DGS5", "maturity": "5Y", "rate_bps": "425"},
        ])
        self.assertFalse(any(row["rate_bps"] == "0" for row in partitions[2026]))

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
            manifest = profile_file(self.root, path, SCHEMAS["historical_rates"])
        self.assertEqual((manifest.row_count, manifest.start_time, manifest.end_time), (1, "2026-08-01", "2026-08-01"))
        self.assertEqual(manifest.sha256, "85452f8588bfc392267c3ffd46e9cb392f0d0df66f607d97ea2c59607fd8cde0")

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
            manifest = profile_file(self.root, path, SCHEMAS["historical_rates"])
        self.assertEqual(manifest.sha256, "85452f8588bfc392267c3ffd46e9cb392f0d0df66f607d97ea2c59607fd8cde0")

    def test_bytes_validator_has_path_validator_parity_and_profile_rejects_duplicate_snapshot(self) -> None:
        path = self.write_rows(
            "duplicate.csv",
            "historical_rates",
            [["2026-08-01", "UST", "DGS2", "2Y", "410"], ["2026-08-01", "UST", "DGS2", "2Y", "410"]],
        )
        data = path.read_bytes()
        for validator_input in (path, data):
            with self.subTest(validator_input=type(validator_input).__name__):
                validator = validate_csv if isinstance(validator_input, Path) else validate_csv_bytes
                with self.assertRaisesRegex(SchemaValidationError, "duplicate key"):
                    validator(SCHEMAS["historical_rates"], validator_input)
        with patch("data_pipeline.contracts.validate_csv", return_value=1):
            with self.assertRaisesRegex(SchemaValidationError, "duplicate key"):
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


class MigrationStagingTests(unittest.TestCase):
    """The production change caught here is a stage that silently loses, mutates,
    or nondeterministically rewrites an approved input."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name) / "repo"
        _populate_migration_repo(self.repo)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write(self, relative: str, header: list[str], rows: list[list[str]]) -> None:
        _write_migration_fixture(self.repo, relative, header, rows)

    def _input_hashes(self) -> dict[str, str]:
        return {path.relative_to(self.repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((self.repo / "data").glob("*.csv"))}

    @staticmethod
    def _copy_partitions(partitions: dict[int, list[dict[str, str]]]) -> dict[int, list[dict[str, str]]]:
        return {year: [dict(row) for row in rows] for year, rows in partitions.items()}

    @staticmethod
    def _read_report(path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_stages_supported_rules_with_exact_report_reconciliation_and_deterministic_bytes(self) -> None:
        before = self._input_hashes()
        original_socket = socket.socket

        def no_socket(*args: object, **kwargs: object) -> object:
            raise AssertionError("migration must never open a network socket")

        socket.socket = no_socket
        try:
            from data_pipeline.migration import REPORT_COLUMNS, stage_migration
            first = stage_migration(self.repo, self.repo / "stage-a", self.repo / "docs/verification/report-a.csv")
            second = stage_migration(self.repo, self.repo / "stage-b", self.repo / "docs/verification/report-b.csv")
        finally:
            socket.socket = original_socket
        self.assertEqual(before, self._input_hashes())
        self.assertTrue(first.all_passed)
        self.assertEqual(first.output_hashes, second.output_hashes)
        self.assertEqual(first.report_path.read_bytes(), second.report_path.read_bytes())
        with first.report_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, list(REPORT_COLUMNS))
            report = list(reader)
        self.assertEqual([row["rule_id"] for row in report], ["cme_swap_master", "swap_rates", "treasury_futures", "treasury_futures_master", "treasury_rates"])
        self.assertEqual(
            {row["rule_id"]: (row["source_key_count"], row["output_key_count"]) for row in report},
            {
                "cme_swap_master": ("4", "4"),
                "swap_rates": ("4", "4"),
                "treasury_futures": ("4", "4"),
                "treasury_futures_master": ("4", "4"),
                "treasury_rates": ("8", "8"),
            },
        )
        for row in report:
            self.assertEqual(row["status"], "pass")
            self.assertEqual(row["expected_key_digest"], row["actual_key_digest"])
            self.assertEqual(row["expected_value_digest"], row["actual_value_digest"])
            self.assertEqual(row["timing_certainty"], "assumed")
            self.assertEqual(row["timing_rule_id"], "p24-market-close-v1")
            self.assertEqual(row["timing_matrix_digest"], "3d4a5df7dedb5c96291891fa7b3383a0b746d710fa1dcf133beaa089c5c56106")
            self.assertEqual(row["scope"], "5 consumed top-level inputs; 1482 catalog artifacts excluded (including 1474 Eris cache files and r2 inventory)")
            spots = json.loads(row["spot_evidence"])
            self.assertEqual([spot["position"] for spot in spots], ["first", "middle", "last"])
            self.assertTrue(all(spot["expected_output"] == spot["actual_output"] for spot in spots))
            self.assertTrue(all(spot["source"] for spot in spots))
        rates = self.repo / "stage-a/data/source/rates/rates_2026.csv"
        settlements = self.repo / "stage-a/data/source/futures/futures_settlements_2026.csv"
        market = self.repo / "stage-a/data/canonical/market/daily_market_2026.csv"
        with rates.open(encoding="utf-8", newline="") as handle:
            rate_rows = list(csv.DictReader(handle))
        with settlements.open(encoding="utf-8", newline="") as handle:
            settlement_rows = list(csv.DictReader(handle))
        with market.open(encoding="utf-8", newline="") as handle:
            market_rows = list(csv.DictReader(handle))
        self.assertEqual(rate_rows[0], {"observation_date": "2026-08-01", "source": "NYFED", "series_id": "EFFR", "maturity": "ON", "rate_bps": "434"})
        self.assertEqual(settlement_rows[0]["instrument_id"], "ERIS-YIT-202609")
        self.assertEqual((market_rows[0]["instrument_id"], market_rows[len(market_rows) // 2]["instrument_id"], market_rows[-1]["instrument_id"]), ("ERIS-YIT", "YAHOO-CONTINUOUS-ZF", "YAHOO-CONTINUOUS-ZT"))
        eris = [row for row in market_rows if row["source"] == "ERIS"]
        yahoo = [row for row in market_rows if row["source"] == "YAHOO"]
        self.assertTrue(all(row["classification"] == "exact" and row["proxy_label"] == "" for row in eris))
        self.assertTrue(all(row["classification"] == "proxy" and row["proxy_label"] for row in yahoo))

    def test_independent_lineage_rejects_schema_valid_wrong_canonicalizer_keys_and_values(self) -> None:
        from data_pipeline import migration

        def wrong_rates(path: Path) -> dict[int, list[dict[str, str]]]:
            result = self._copy_partitions(canonicalize_rates(path))
            first = result[min(result)][0]
            first["series_id"] = "A-EFFR"
            first["rate_bps"] = "999"
            return result

        def wrong_futures(swap_path: Path, treasury_path: Path) -> FuturesCanonicalization:
            result = canonicalize_futures(swap_path, treasury_path)
            settlements = self._copy_partitions(result.settlements_by_year)
            risks = self._copy_partitions(result.risk_by_year)
            settlements[min(settlements)][0]["instrument_id"] = "ERIS-YIS-202512"
            settlements[min(settlements)][0]["settlement_price"] = "77"
            risks[min(risks)][0]["instrument_id"] = "ERIS-YIS-202512"
            risks[min(risks)][0]["dv01_usd_per_bp"] = "77"
            return FuturesCanonicalization(settlements, risks)

        def wrong_market(swap_path: Path, treasury_path: Path, timing: object) -> dict[int, list[dict[str, str]]]:
            result = self._copy_partitions(canonicalize_daily_market(swap_path, treasury_path, timing))
            first = result[min(result)][0]
            first["instrument_id"] = "ERIS-YIS"
            first["value"] = "77"
            return result

        cases = (
            ("canonicalize_rates", wrong_rates),
            ("canonicalize_futures", wrong_futures),
            ("canonicalize_daily_market", wrong_market),
        )
        for index, (name, replacement) in enumerate(cases):
            with self.subTest(canonicalizer=name), patch.object(migration, name, replacement):
                stage = self.repo / f"wrong-stage-{index}"
                report_path = self.repo / "docs/verification" / f"wrong-report-{index}.csv"
                result = migration.stage_migration(self.repo, stage, report_path)
                self.assertFalse(result.all_passed)
                report = self._read_report(report_path)
                failed = [row for row in report if row["status"] == "fail"]
                self.assertTrue(failed)
                self.assertTrue(any(
                    row["expected_key_digest"] != row["actual_key_digest"]
                    or row["expected_value_digest"] != row["actual_value_digest"]
                    for row in failed
                ))
                self.assertTrue(stage.is_dir())

    def test_staging_reconciles_only_present_rate_series(self) -> None:
        from data_pipeline.migration import stage_migration

        source = self.repo / "data/treasury_rates.csv"
        with source.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["dgs2"] = ""
        with source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=RATE_HEADER, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        result = stage_migration(self.repo, self.repo / "present-stage", self.repo / "docs/verification/present-report.csv")
        self.assertTrue(result.all_passed)
        report = {row["rule_id"]: row for row in self._read_report(result.report_path)}
        self.assertEqual((report["treasury_rates"]["source_key_count"], report["treasury_rates"]["output_key_count"]), ("7", "7"))
        with (result.staging_root / "data/source/rates/rates_2025.csv").open(encoding="utf-8", newline="") as handle:
            staged = list(csv.DictReader(handle))
        self.assertFalse(any(row["observation_date"] == "2025-12-31" and row["series_id"] == "DGS2" for row in staged))

    def test_descriptor_path_race_fails_and_removes_new_stage(self) -> None:
        from data_pipeline.migration import MigrationError, stage_migration

        source = self.repo / "data/treasury_rates.csv"
        replacement = self.repo / "data/treasury-race.tmp"
        replacement.write_bytes(source.read_bytes())
        original_open = Path.open
        raced = False

        def racing_open(path: Path, *args: object, **kwargs: object) -> object:
            nonlocal raced
            mode = args[0] if args else kwargs.get("mode", "r")
            if path.name == source.name and mode == "rb" and not raced:
                raced = True
                os.replace(replacement, source)
            return original_open(path, *args, **kwargs)

        stage = self.repo / "raced-stage"
        report = self.repo / "docs/verification/raced-report.csv"
        with patch.object(Path, "open", new=racing_open), self.assertRaisesRegex(MigrationError, "changed before snapshot"):
            stage_migration(self.repo, stage, report)
        self.assertTrue(raced)
        self.assertFalse(stage.exists())
        self.assertFalse(report.exists())

    def test_rejects_path_escape_symlink_input_and_nonempty_staging(self) -> None:
        from data_pipeline.migration import MigrationError, stage_migration
        with self.assertRaisesRegex(MigrationError, "escapes"):
            stage_migration(self.repo, self.repo.parent / "outside", self.repo / "docs/verification/report.csv")
        occupied = self.repo / "occupied"
        occupied.mkdir()
        (occupied / "old").write_text("old", encoding="utf-8")
        with self.assertRaisesRegex(MigrationError, "empty"):
            stage_migration(self.repo, occupied, self.repo / "docs/verification/report.csv")
        source = self.repo / "data/treasury_rates.csv"
        linked = self.repo / "data/linked.csv"
        try:
            linked.symlink_to(source)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        source.unlink()
        linked.rename(source)
        with self.assertRaisesRegex(MigrationError, "symlink"):
            stage_migration(self.repo, self.repo / "stage", self.repo / "docs/verification/report.csv")

    def test_second_staging_root_under_data_is_not_discovered_as_an_input(self) -> None:
        from data_pipeline.migration import stage_migration
        stage_migration(self.repo, self.repo / "data/stage-a", self.repo / "docs/verification/report-a.csv")
        second = stage_migration(self.repo, self.repo / "data/stage-b", self.repo / "docs/verification/report-b.csv")
        self.assertTrue(second.all_passed)

    def test_rejects_report_overwrite_of_sources_staging_or_nonverification_paths_before_mutation(self) -> None:
        from data_pipeline.migration import MigrationError, stage_migration
        source = self.repo / "data/treasury_rates.csv"
        before = source.read_bytes()
        for report in (source, self.repo / "stage", self.repo / "report.csv"):
            with self.subTest(report=report), self.assertRaisesRegex(MigrationError, "report"):
                stage_migration(self.repo, self.repo / "stage", report)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse((self.repo / "stage").exists())


class MigrationPublicationTests(unittest.TestCase):
    """The production changes caught here are stale, undeclared, partial, or
    non-transactional publication of migration evidence."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name) / "repo"
        _populate_migration_repo(self.repo)
        self.source_hashes = self._hashes((self.repo / "data").glob("*.csv"))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _hashes(paths: object) -> dict[str, str]:
        return {
            path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)
        }

    def _stage(self, name: str = "publish-stage") -> object:
        from data_pipeline.migration import stage_migration
        return stage_migration(
            self.repo,
            self.repo / name,
            self.repo / "docs/verification" / f"{name}.csv",
        )

    def test_publication_requires_the_fresh_passing_result_and_unchanged_evidence(self) -> None:
        from data_pipeline.migration import MigrationError, publish_migration

        result = self._stage()
        with self.assertRaisesRegex(MigrationError, "fully passing"):
            publish_migration(replace(result, all_passed=False), self.repo)
        with self.assertRaisesRegex(MigrationError, "fresh"):
            publish_migration(replace(result, output_hashes={**result.output_hashes, "data/raw_price_data.csv": "0" * 64}), self.repo)

        with result.report_path.open("r+", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
            rows[0]["status"] = "fail"
            handle.seek(0)
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            handle.truncate()
        with self.assertRaisesRegex(MigrationError, "report"):
            publish_migration(result, self.repo)
        self.assertEqual(self.source_hashes, self._hashes((self.repo / "data").glob("*.csv")))

    def test_publication_rejects_changed_staged_manifest_or_source_bytes(self) -> None:
        from data_pipeline.migration import MigrationError, publish_migration

        mutations = (
            ("staged", "data/source/rates/rates_2026.csv"),
            ("manifest", "data/manifests/p24_inputs.csv"),
            ("source", "../data/treasury_rates.csv"),
        )
        for index, (label, relative) in enumerate(mutations):
            with self.subTest(label=label):
                result = self._stage(f"stale-{index}")
                target = result.staging_root / relative
                target.write_bytes(target.read_bytes() + b"\n")
                with self.assertRaisesRegex(MigrationError, "changed|hash|manifest|source"):
                    publish_migration(result, self.repo)

    def test_replacement_failure_rolls_back_every_destination_and_cleans_siblings(self) -> None:
        from data_pipeline import migration

        result = self._stage()
        originals: dict[Path, bytes] = {}
        for index, relative in enumerate(sorted(result.output_hashes)):
            destination = self.repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            originals[destination] = f"old-{index}\n".encode()
            destination.write_bytes(originals[destination])

        real_link = os.link
        installs = 0

        def fail_second_install(source: object, destination: object, *args: object, **kwargs: object) -> None:
            nonlocal installs
            if ".p24-publish-" in Path(source).name:
                installs += 1
                if installs == 2:
                    raise OSError("injected replacement failure")
            real_link(source, destination, *args, **kwargs)

        with patch.object(migration.os, "link", side_effect=fail_second_install):
            with self.assertRaisesRegex(migration.MigrationError, "publication failed") as caught:
                migration.publish_migration(result, self.repo)
        self.assertNotIn("rollback was incomplete", str(caught.exception))
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        leftovers = [path for path in self.repo.rglob("*") if ".p24-publish-" in path.name or ".p24-backup-" in path.name]
        self.assertEqual(leftovers, [])

    def test_post_replace_verify_failure_is_journaled_before_rollback(self) -> None:
        from data_pipeline import migration

        result = self._stage()
        originals: dict[Path, bytes] = {}
        for index, relative in enumerate(sorted(result.output_hashes)):
            destination = self.repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            originals[destination] = f"original-{index}\n".encode()
            destination.write_bytes(originals[destination])
        target = self.repo / sorted(result.output_hashes)[0]
        real_verify = migration._DirectoryGuard.verify
        injected = False

        def fail_immediately_after_replace(guard: object) -> None:
            nonlocal injected
            real_verify(guard)
            claim_exists = any(target.parent.glob(f".{target.name}.p24-claim-*.tmp"))
            if not injected and not target.exists() and claim_exists:
                injected = True
                raise migration.MigrationError("injected post-replace verification failure")

        with patch.object(migration._DirectoryGuard, "verify", autospec=True, side_effect=fail_immediately_after_replace):
            with self.assertRaisesRegex(migration.MigrationError, "post-replace verification failure"):
                migration.publish_migration(result, self.repo)
        self.assertTrue(injected)
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        leftovers = [path for path in self.repo.rglob("*") if any(marker in path.name for marker in (".p24-publish-", ".p24-backup-", ".p24-claim-"))]
        self.assertEqual(leftovers, [])

    def test_post_link_verify_failure_is_journaled_before_rollback(self) -> None:
        from data_pipeline import migration

        result = self._stage()
        first_relative = sorted(result.output_hashes)[0]
        target = self.repo / first_relative
        real_verify = migration._DirectoryGuard.verify
        injected = False

        def fail_immediately_after_link(guard: object) -> None:
            nonlocal injected
            real_verify(guard)
            if not injected and target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == result.output_hashes[first_relative]:
                injected = True
                raise migration.MigrationError("injected post-link verification failure")

        with patch.object(migration._DirectoryGuard, "verify", autospec=True, side_effect=fail_immediately_after_link):
            with self.assertRaisesRegex(migration.MigrationError, "post-link verification failure"):
                migration.publish_migration(result, self.repo)
        self.assertTrue(injected)
        self.assertFalse(any((self.repo / relative).exists() for relative in result.output_hashes))
        leftovers = [path for path in self.repo.rglob("*") if any(marker in path.name for marker in (".p24-publish-", ".p24-backup-", ".p24-claim-"))]
        self.assertEqual(leftovers, [])

    def test_claim_is_journaled_before_unlink_guard_can_fail(self) -> None:
        from data_pipeline import migration

        result = self._stage()
        originals: dict[Path, bytes] = {}
        for index, relative in enumerate(sorted(result.output_hashes)):
            destination = self.repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            originals[destination] = f"original-{index}\n".encode()
            destination.write_bytes(originals[destination])
        target = self.repo / sorted(result.output_hashes)[0]
        real_verify = migration._DirectoryGuard.verify
        claim_verifications = 0
        injected = False

        def fail_before_claim_unlink(guard: object) -> None:
            nonlocal claim_verifications, injected
            real_verify(guard)
            if any(target.parent.glob(f".{target.name}.p24-claim-*.tmp")):
                claim_verifications += 1
                if claim_verifications == 2:
                    injected = True
                    raise migration.MigrationError("injected claim unlink guard failure")

        with patch.object(migration._DirectoryGuard, "verify", autospec=True, side_effect=fail_before_claim_unlink):
            with self.assertRaisesRegex(migration.MigrationError, "claim unlink guard failure"):
                migration.publish_migration(result, self.repo)
        self.assertTrue(injected)
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        leftovers = [path for path in self.repo.rglob("*") if any(marker in path.name for marker in (".p24-publish-", ".p24-backup-", ".p24-claim-"))]
        self.assertEqual(leftovers, [])

    def test_cleanup_failure_does_not_mask_rollback_or_skip_guards_and_siblings(self) -> None:
        from data_pipeline import migration

        result = self._stage()
        originals: dict[Path, bytes] = {}
        for index, relative in enumerate(sorted(result.output_hashes)):
            destination = self.repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            originals[destination] = f"original-{index}\n".encode()
            destination.write_bytes(originals[destination])

        real_acquire = migration._acquire_directory_guard
        real_close = migration._DirectoryGuard.close
        real_link = os.link
        real_unlink = migration._unlink_sibling
        acquired: list[object] = []
        closed: list[object] = []
        cleanup_attempts: list[Path] = []
        installs = 0
        cleanup_failed = False

        def record_acquire(path: Path) -> object:
            guard = real_acquire(path)
            acquired.append(guard)
            return guard

        def record_close(guard: object) -> None:
            try:
                real_close(guard)
            finally:
                closed.append(guard)

        def fail_second_install(source: object, destination: object, *args: object, **kwargs: object) -> None:
            nonlocal installs
            if ".p24-publish-" in Path(source).name:
                installs += 1
                if installs == 2:
                    raise OSError("injected transaction failure")
            real_link(source, destination, *args, **kwargs)

        def fail_first_cleanup(path: Path, guards: object, *, verify: bool = True) -> None:
            nonlocal cleanup_failed
            if not verify:
                cleanup_attempts.append(path)
                real_unlink(path, guards, verify=False)
                if not cleanup_failed:
                    cleanup_failed = True
                    raise OSError("injected cleanup failure")
                return
            real_unlink(path, guards, verify=verify)

        caught: BaseException | None = None
        try:
            with (
                patch.object(migration, "_acquire_directory_guard", side_effect=record_acquire),
                patch.object(migration._DirectoryGuard, "close", autospec=True, side_effect=record_close),
                patch.object(migration.os, "link", side_effect=fail_second_install),
                patch.object(migration, "_unlink_sibling", side_effect=fail_first_cleanup),
            ):
                try:
                    migration.publish_migration(result, self.repo)
                except BaseException as error:
                    caught = error
        finally:
            for guard in acquired:
                if guard not in closed:
                    real_close(guard)
        self.assertIsInstance(caught, migration.MigrationError)
        self.assertRegex(str(caught), "publication failed")
        self.assertTrue(cleanup_failed)
        self.assertEqual(len(cleanup_attempts), 3 * len(result.output_hashes))
        self.assertEqual({id(guard) for guard in acquired}, {id(guard) for guard in closed})
        self.assertTrue(any("injected cleanup failure" in note for note in getattr(caught, "__notes__", ())))
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        leftovers = [path for path in self.repo.rglob("*") if any(marker in path.name for marker in (".p24-publish-", ".p24-backup-", ".p24-claim-"))]
        self.assertEqual(leftovers, [])

    def test_cleanup_failure_after_commit_reports_committed_outcome_and_closes_guards(self) -> None:
        from data_pipeline import migration

        result = self._stage()
        real_acquire = migration._acquire_directory_guard
        real_close = migration._DirectoryGuard.close
        real_unlink = migration._unlink_sibling
        acquired: list[object] = []
        closed: list[object] = []
        cleanup_attempts: list[Path] = []
        cleanup_failed = False

        def record_acquire(path: Path) -> object:
            guard = real_acquire(path)
            acquired.append(guard)
            return guard

        def record_close(guard: object) -> None:
            try:
                real_close(guard)
            finally:
                closed.append(guard)

        def fail_first_cleanup(path: Path, guards: object, *, verify: bool = True) -> None:
            nonlocal cleanup_failed
            if not verify:
                cleanup_attempts.append(path)
                real_unlink(path, guards, verify=False)
                if not cleanup_failed:
                    cleanup_failed = True
                    raise OSError("injected committed cleanup failure")
                return
            real_unlink(path, guards, verify=verify)

        caught: BaseException | None = None
        try:
            with (
                patch.object(migration, "_acquire_directory_guard", side_effect=record_acquire),
                patch.object(migration._DirectoryGuard, "close", autospec=True, side_effect=record_close),
                patch.object(migration, "_unlink_sibling", side_effect=fail_first_cleanup),
            ):
                try:
                    migration.publish_migration(result, self.repo)
                except BaseException as error:
                    caught = error
        finally:
            for guard in acquired:
                if guard not in closed:
                    real_close(guard)
        self.assertIsInstance(caught, migration.MigrationError)
        self.assertRegex(str(caught), "publication committed but cleanup was incomplete")
        self.assertTrue(cleanup_failed)
        self.assertEqual(len(cleanup_attempts), len(result.output_hashes))
        self.assertEqual({id(guard) for guard in acquired}, {id(guard) for guard in closed})
        self.assertEqual(
            {self.repo / relative: hashlib.sha256((self.repo / relative).read_bytes()).hexdigest() for relative in result.output_hashes},
            {self.repo / relative: digest for relative, digest in result.output_hashes.items()},
        )
        leftovers = [path for path in self.repo.rglob("*") if any(marker in path.name for marker in (".p24-publish-", ".p24-backup-", ".p24-claim-"))]
        self.assertEqual(leftovers, [])

    def test_success_publishes_only_declared_outputs_preserves_sources_and_leaves_no_siblings(self) -> None:
        from data_pipeline.migration import publish_migration

        result = self._stage()
        stage_before = self._hashes(path for path in result.staging_root.rglob("*") if path.is_file())
        report_before = result.report_path.read_bytes()
        published = publish_migration(result, self.repo)
        expected = [self.repo / relative for relative in sorted(result.output_hashes)]
        self.assertEqual(published, expected)
        self.assertEqual(
            {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in expected},
            {self.repo / relative: digest for relative, digest in sorted(result.output_hashes.items())},
        )
        self.assertEqual(self.source_hashes, self._hashes((self.repo / "data").glob("*.csv")))
        self.assertEqual(stage_before, self._hashes(path for path in result.staging_root.rglob("*") if path.is_file()))
        self.assertEqual(report_before, result.report_path.read_bytes())
        self.assertFalse((self.repo / "data/manifests/p24_run.csv").exists())
        leftovers = [path for path in self.repo.rglob("*") if ".p24-publish-" in path.name or ".p24-backup-" in path.name]
        self.assertEqual(leftovers, [])

    def test_cli_publish_revalidates_an_existing_passing_stage(self) -> None:
        from data_pipeline import migration

        result = self._stage()
        migration._PUBLISHABLE_RESULTS.clear()
        exit_code = migration.main([
            "--repo-root", str(self.repo),
            "--staging-root", str(result.staging_root),
            "--report", str(result.report_path),
            "--publish",
        ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            [self.repo / relative for relative in sorted(result.output_hashes)],
            [path for path in sorted((self.repo / "data").rglob("*.csv")) if path not in [self.repo / source for source in ("data/cme_swap_data.csv", "data/swap_rates.csv", "data/treasury_futures.csv", "data/treasury_futures_data.csv", "data/treasury_rates.csv")]],
        )

    def test_destination_race_is_rejected_without_installing_any_staged_bytes(self) -> None:
        from data_pipeline import migration

        result = self._stage()
        first_relative = sorted(result.output_hashes)[0]
        destination = self.repo / first_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"original\n")
        real_verify = migration._verify_publication_evidence
        calls = 0

        def race_after_preparation(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            verified = real_verify(*args, **kwargs)
            if calls == 2:
                destination.write_bytes(b"external-race\n")
            return verified

        with patch.object(migration, "_verify_publication_evidence", side_effect=race_after_preparation), self.assertRaisesRegex(migration.MigrationError, "destination changed"):
            migration.publish_migration(result, self.repo)
        self.assertEqual(destination.read_bytes(), b"external-race\n")
        for relative in sorted(result.output_hashes)[1:]:
            self.assertFalse((self.repo / relative).exists())
        leftovers = [path for path in self.repo.rglob("*") if ".p24-publish-" in path.name or ".p24-backup-" in path.name]
        self.assertEqual(leftovers, [])

    def test_final_no_clobber_install_rejects_a_destination_created_after_the_last_check(self) -> None:
        from data_pipeline import migration

        result = self._stage()
        first_relative = sorted(result.output_hashes)[0]
        destination = self.repo / first_relative
        real_link = os.link
        raced = False

        def create_destination_before_install(source: object, target: object, *args: object, **kwargs: object) -> None:
            nonlocal raced
            if not raced and ".p24-publish-" in Path(source).name:
                raced = True
                Path(target).write_bytes(b"late-race\n")
            real_link(source, target, *args, **kwargs)

        with patch.object(migration.os, "link", side_effect=create_destination_before_install), self.assertRaisesRegex(migration.MigrationError, "publication failed"):
            migration.publish_migration(result, self.repo)
        self.assertTrue(raced)
        self.assertEqual(destination.read_bytes(), b"late-race\n")
        leftovers = [path for path in self.repo.rglob("*") if any(marker in path.name for marker in (".p24-publish-", ".p24-backup-", ".p24-claim-"))]
        self.assertEqual(leftovers, [])

    @unittest.skipUnless(os.name == "nt", "Windows directory replacement lock")
    def test_destination_parent_cannot_be_renamed_during_final_install(self) -> None:
        from data_pipeline import migration

        result = self._stage()
        first_relative = sorted(result.output_hashes)[0]
        parent = (self.repo / first_relative).parent
        moved = parent.with_name(f"{parent.name}-raced")
        real_link = os.link
        attempted = False

        def try_parent_replacement(source: object, target: object, *args: object, **kwargs: object) -> None:
            nonlocal attempted
            if not attempted and ".p24-publish-" in Path(source).name:
                attempted = True
                try:
                    parent.rename(moved)
                except OSError:
                    pass
                else:
                    moved.rename(parent)
                    raise AssertionError("destination parent was not locked against replacement")
            real_link(source, target, *args, **kwargs)

        with patch.object(migration.os, "link", side_effect=try_parent_replacement):
            migration.publish_migration(result, self.repo)
        self.assertTrue(attempted)
        self.assertFalse(moved.exists())


if __name__ == "__main__":
    unittest.main()
