"""Import-only environment smoke checks for tracked entry points."""

import os
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch


class ImportSmokeTests(unittest.TestCase):
    def test_tracked_entry_points_and_lazy_ibkr_client_are_available(self) -> None:
        import raw_price_data  # noqa: F401
        import signal_data  # noqa: F401
        import risk_data  # noqa: F401
        import backtest  # noqa: F401
        import agents.agent_0.run  # noqa: F401
        from agents.agent_0.broker import _load_ib_class

        legacy_cloudflare_script = (
            Path(__file__).resolve().parents[1] / "cloudflare_r2_test.py"
        )
        if legacy_cloudflare_script.is_file():
            with patch.dict(os.environ, {}, clear=True):
                import cloudflare_r2_test

                self.assertEqual(cloudflare_r2_test.TOKEN, "")
                self.assertEqual(cloudflare_r2_test.ACCOUNT_ID, "")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.assertIsNotNone(_load_ib_class())


if __name__ == "__main__":
    unittest.main()
