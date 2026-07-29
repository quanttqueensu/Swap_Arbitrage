"""Import-only environment smoke checks for tracked entry points."""

import unittest
import warnings


class ImportSmokeTests(unittest.TestCase):
    def test_tracked_entry_points_and_lazy_ibkr_client_are_available(self) -> None:
        import raw_price_data  # noqa: F401
        import signal_data  # noqa: F401
        import risk_data  # noqa: F401
        import backtest  # noqa: F401
        import cloudflare_r2_test  # noqa: F401
        import agents.agent_0.run  # noqa: F401
        from agents.agent_0.broker import _load_ib_class

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.assertIsNotNone(_load_ib_class())


if __name__ == "__main__":
    unittest.main()
