"""Import-only environment smoke checks for tracked entry points."""

import unittest


class ImportSmokeTests(unittest.TestCase):
    def test_tracked_entry_points_and_lazy_ibkr_client_are_available(self) -> None:
        import data_pipeline.historical_data.historical_data_builder  # noqa: F401
        import signal_pipeline  # noqa: F401
        import risk_pipeline  # noqa: F401
        import backtesting  # noqa: F401
        import backtesting.__main__  # noqa: F401
        import agents.agent_0.run  # noqa: F401
        from agents.agent_0.broker import _load_ib_class

        self.assertIsNotNone(_load_ib_class())

if __name__ == "__main__":
    unittest.main()
