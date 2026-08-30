from __future__ import annotations

import unittest
from datetime import datetime, time, timezone
from types import SimpleNamespace

from agents.agent_1.market_hours import market_is_open


class MarketHoursTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            timezone="America/New_York",
            market_open_time=time(9, 0),
            market_close_time=time(15, 0),
        )

    def test_open_is_inclusive_and_close_is_exclusive(self):
        self.assertTrue(market_is_open(self.config, datetime(2026, 8, 31, 13, 0, tzinfo=timezone.utc)))
        self.assertFalse(market_is_open(self.config, datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc)))

    def test_weekend_is_closed(self):
        self.assertFalse(market_is_open(self.config, datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)))

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            market_is_open(self.config, datetime(2026, 8, 31, 10, 0))


if __name__ == "__main__":
    unittest.main()
