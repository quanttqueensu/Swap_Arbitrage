from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.agent_1.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "agent1.paper.json"
        self.valid = {
            "account": "DU123456",
            "client_id": 31,
            "market_open_time": "09:00",
            "market_close_time": "15:00",
            "min_days_to_expiry": 14,
            "max_target_age_business_days": 2,
            "max_quote_age_seconds": 10,
            "order_group_timeout_seconds": 30,
            "max_working_order_groups": 2,
            "max_order_groups_per_session": 50,
            "max_gross_dv01": 10000,
            "max_net_dv01": 250,
            "max_residual_dv01_fraction": 0.10,
            "margin_reserve_fraction": 0.15,
            "max_session_loss_usd": 1000,
            "max_drawdown_usd": 1000,
            "max_2y_swap_contracts": 100,
            "max_2y_treasury_contracts": 100,
            "max_5y_swap_contracts": 100,
            "max_5y_treasury_contracts": 100,
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write(self, values: dict[str, object]) -> None:
        self.path.write_text(json.dumps(values), encoding="utf-8")

    def test_loads_valid_paper_configuration(self) -> None:
        self.write(self.valid)
        with patch.dict(os.environ, {"AGENT1_PAPER_CONFIG": str(self.path)}, clear=False):
            config = load_config()

        self.assertEqual(config.account, "DU123456")
        self.assertEqual(config.client_id, 31)
        self.assertEqual(config.poll_interval_seconds, 30)
        self.assertEqual(config.timezone, "America/New_York")
        self.assertEqual(config.market_open_time.isoformat(timespec="minutes"), "09:00")
        self.assertEqual(config.market_close_time.isoformat(timespec="minutes"), "15:00")
        self.assertEqual(config.min_days_to_expiry, 14)
        self.assertFalse(config.live_target_enabled)


    def test_live_target_enablement_is_explicit_and_boolean(self) -> None:
        values = dict(self.valid, live_target_enabled=True)
        self.write(values)
        with patch.dict(os.environ, {"AGENT1_PAPER_CONFIG": str(self.path)}, clear=False):
            config = load_config()
        self.assertTrue(config.live_target_enabled)

        values["live_target_enabled"] = "true"
        self.write(values)
        with patch.dict(os.environ, {"AGENT1_PAPER_CONFIG": str(self.path)}, clear=False):
            with self.assertRaisesRegex(ConfigError, "live_target_enabled"):
                load_config()

    def test_rejects_agent_zero_client_id(self) -> None:
        values = dict(self.valid, client_id=30)
        self.write(values)
        with patch.dict(os.environ, {"AGENT1_PAPER_CONFIG": str(self.path)}, clear=False):
            with self.assertRaisesRegex(ConfigError, "distinct"):
                load_config()

    def test_rejects_non_paper_account(self) -> None:
        values = dict(self.valid, account="U123456")
        self.write(values)
        with patch.dict(os.environ, {"AGENT1_PAPER_CONFIG": str(self.path)}, clear=False):
            with self.assertRaisesRegex(ConfigError, "DU"):
                load_config()

    def test_rejects_zero_contract_cap(self) -> None:
        values = dict(self.valid, max_2y_swap_contracts=0)
        self.write(values)
        with patch.dict(os.environ, {"AGENT1_PAPER_CONFIG": str(self.path)}, clear=False):
            with self.assertRaisesRegex(ConfigError, "max_2y_swap_contracts"):
                load_config()

    def test_rejects_fraction_outside_unit_interval(self) -> None:
        values = dict(self.valid, margin_reserve_fraction=1.1)
        self.write(values)
        with patch.dict(os.environ, {"AGENT1_PAPER_CONFIG": str(self.path)}, clear=False):
            with self.assertRaisesRegex(ConfigError, "margin_reserve_fraction"):
                load_config()

    def test_rejects_market_window_that_does_not_close_after_open(self) -> None:
        values = dict(self.valid, market_close_time="09:00")
        self.write(values)
        with patch.dict(os.environ, {"AGENT1_PAPER_CONFIG": str(self.path)}, clear=False):
            with self.assertRaisesRegex(ConfigError, "market"):
                load_config()

    def test_rejects_zero_minimum_days_to_expiry(self) -> None:
        values = dict(self.valid, min_days_to_expiry=0)
        self.write(values)
        with patch.dict(os.environ, {"AGENT1_PAPER_CONFIG": str(self.path)}, clear=False):
            with self.assertRaisesRegex(ConfigError, "min_days_to_expiry"):
                load_config()


if __name__ == "__main__":
    unittest.main()
