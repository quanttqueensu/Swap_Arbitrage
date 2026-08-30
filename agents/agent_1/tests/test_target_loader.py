from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.agent_1.target_loader import TargetValidationError, load_daily_target


FIELDNAMES = [
    "date",
    "risk_allowed",
    "risk_block_reason",
    "swap_futures_contracts_rounded_2y",
    "treasury_futures_contracts_rounded_2y",
    "swap_futures_contracts_rounded_5y",
    "treasury_futures_contracts_rounded_5y",
]


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


class TargetLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "risk_data.csv"
        self.now = datetime(2026, 8, 31, 10, 0, tzinfo=ZoneInfo("America/New_York"))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def valid_row(self, **changes: object) -> dict[str, object]:
        row: dict[str, object] = {
            "date": "2026-08-31",
            "risk_allowed": 1,
            "risk_block_reason": "",
            "swap_futures_contracts_rounded_2y": 12,
            "treasury_futures_contracts_rounded_2y": -6,
            "swap_futures_contracts_rounded_5y": -8,
            "treasury_futures_contracts_rounded_5y": 8,
        }
        row.update(changes)
        return row

    def test_loads_latest_valid_target_with_signed_quantities(self) -> None:
        write_rows(
            self.path,
            [
                self.valid_row(date="2026-08-28", swap_futures_contracts_rounded_2y=10),
                self.valid_row(),
            ],
        )

        target = load_daily_target(self.path, now=self.now, max_age_business_days=1)

        self.assertEqual(target.as_of.isoformat(), "2026-08-31")
        self.assertEqual(target.for_maturity("2Y").swap_qty, 12)
        self.assertEqual(target.for_maturity("2Y").treasury_qty, -6)
        self.assertEqual(target.for_maturity("5Y").swap_qty, -8)
        self.assertEqual(target.for_maturity("5Y").treasury_qty, 8)

    def test_target_version_is_stable_when_non_latest_rows_change(self) -> None:
        write_rows(self.path, [self.valid_row(date="2026-08-28"), self.valid_row()])
        first = load_daily_target(self.path, now=self.now, max_age_business_days=1)

        write_rows(
            self.path,
            [
                self.valid_row(date="2026-08-28", swap_futures_contracts_rounded_2y=999),
                self.valid_row(),
            ],
        )
        second = load_daily_target(self.path, now=self.now, max_age_business_days=1)

        self.assertEqual(first.version, second.version)

    def test_target_version_changes_when_execution_quantity_changes(self) -> None:
        write_rows(self.path, [self.valid_row()])
        first = load_daily_target(self.path, now=self.now, max_age_business_days=1)

        write_rows(self.path, [self.valid_row(swap_futures_contracts_rounded_2y=13)])
        second = load_daily_target(self.path, now=self.now, max_age_business_days=1)

        self.assertNotEqual(first.version, second.version)

    def test_rejects_blocked_latest_row_instead_of_falling_back(self) -> None:
        write_rows(
            self.path,
            [
                self.valid_row(date="2026-08-28"),
                self.valid_row(risk_allowed=0, risk_block_reason="portfolio:net_dv01_limit"),
            ],
        )

        with self.assertRaisesRegex(TargetValidationError, "risk_allowed"):
            load_daily_target(self.path, now=self.now, max_age_business_days=1)

    def test_rejects_stale_target_by_new_york_business_days(self) -> None:
        write_rows(self.path, [self.valid_row(date="2026-08-27")])

        with self.assertRaisesRegex(TargetValidationError, "stale"):
            load_daily_target(self.path, now=self.now, max_age_business_days=1)

    def test_weekend_does_not_age_target(self) -> None:
        friday = self.valid_row(date="2026-08-28")
        write_rows(self.path, [friday])

        target = load_daily_target(self.path, now=self.now, max_age_business_days=1)

        self.assertEqual(target.age_business_days, 1)

    def test_rejects_fractional_contract_quantity(self) -> None:
        write_rows(self.path, [self.valid_row(swap_futures_contracts_rounded_2y="12.5")])

        with self.assertRaisesRegex(TargetValidationError, "integer"):
            load_daily_target(self.path, now=self.now, max_age_business_days=1)


if __name__ == "__main__":
    unittest.main()
