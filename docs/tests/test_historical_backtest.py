from datetime import timezone
from decimal import Decimal
import unittest

import pandas as pd

from backtesting.historical import _events_from_frame


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

    def test_conflicting_duplicate_instruments_fail_closed(self):
        conflicting = historical_frame()
        conflicting["swap_ticker_5y"] = "YITH24"
        conflicting["swap_price_5y"] = 100.0
        conflicting["swap_dv01_per_contract_5y"] = 19.0

        with self.assertRaisesRegex(RuntimeError, "conflicting duplicate instrument"):
            _events_from_frame(conflicting)
