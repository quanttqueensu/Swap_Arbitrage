import unittest
from datetime import time
from decimal import Decimal
from unittest.mock import patch

from agents.agent_1.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def test_loads_configured_paper_settings(self) -> None:
        with patch("agents.agent_1.config.PAPER_ACCOUNT", "DU123456"):
            config = load_config()

        self.assertEqual(config.account, "DU123456")
        self.assertEqual(config.client_id, 31)
        self.assertEqual(config.market_open_time, time(9, 0))
        self.assertEqual(config.market_close_time, time(15, 0))
        self.assertEqual(config.max_quote_age_seconds, Decimal("10"))

    def test_requires_a_paper_account_in_the_config_module(self) -> None:
        with patch("agents.agent_1.config.PAPER_ACCOUNT", ""):
            with self.assertRaisesRegex(ConfigError, "PAPER_ACCOUNT"):
                load_config()

    def test_rejects_agent_zero_client_id(self) -> None:
        with patch("agents.agent_1.config.PAPER_ACCOUNT", "DU123456"), patch(
            "agents.agent_1.config.CLIENT_ID", 30
        ):
            with self.assertRaisesRegex(ConfigError, "distinct"):
                load_config()

    def test_rejects_invalid_market_window(self) -> None:
        with patch("agents.agent_1.config.PAPER_ACCOUNT", "DU123456"), patch(
            "agents.agent_1.config.MARKET_CLOSE_TIME", time(9, 0)
        ):
            with self.assertRaisesRegex(ConfigError, "market"):
                load_config()


if __name__ == "__main__":
    unittest.main()
