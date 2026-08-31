from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import pandas as pd

from data_pipeline.live_data_pipeline.model_state import (
    load_daily_observation,
    load_model_state,
    load_signal_state,
    save_signal_state,
)
from strategy.live_signal import LIVE_SIGNAL_STRATEGY_VERSION


class LiveModelStateTests(unittest.TestCase):
    def test_loader_is_causal_and_ignores_future_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.csv"
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            rows = []
            for i in range(70):
                rows.append(
                    {
                        "timestamp_utc": (start + timedelta(days=i)).isoformat(),
                        "maturity": "2Y",
                        "strategy_version": LIVE_SIGNAL_STRATEGY_VERSION,
                        "spread_bps": str(i),
                    }
                )
            rows.append(
                {
                    "timestamp_utc": (start + timedelta(days=100)).isoformat(),
                    "maturity": "2Y",
                    "strategy_version": LIVE_SIGNAL_STRATEGY_VERSION,
                    "spread_bps": "10000",
                }
            )
            pd.DataFrame(rows).to_csv(path, index=False)

            as_of = start + timedelta(days=69, hours=1)
            model = load_model_state(path, "2Y", as_of)

            expected = pd.Series(range(70), dtype="float64")
            self.assertEqual(model.observation_count, 70)
            self.assertEqual(model.version, LIVE_SIGNAL_STRATEGY_VERSION)
            self.assertEqual(model.mean_bps, Decimal(str(expected.mean())))
            self.assertEqual(model.std_bps, Decimal(str(expected.std())))

    def test_loader_uses_only_trailing_252_observations(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.csv"
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)
            rows = [
                {
                    "timestamp_utc": (start + timedelta(hours=i)).isoformat(),
                    "maturity": "5Y",
                    "strategy_version": LIVE_SIGNAL_STRATEGY_VERSION,
                    "spread_bps": str(i),
                }
                for i in range(300)
            ]
            pd.DataFrame(rows).to_csv(path, index=False)
            model = load_model_state(path, "5Y", start + timedelta(hours=300))
            expected = pd.Series(range(48, 300), dtype="float64")
            self.assertEqual(model.observation_count, 252)
            self.assertEqual(model.mean_bps, Decimal(str(expected.mean())))

    def test_wrong_strategy_version_is_not_used(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.csv"
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            rows = [
                {
                    "timestamp_utc": (start + timedelta(days=i)).isoformat(),
                    "maturity": "2Y",
                    "strategy_version": "legacy_dgs",
                    "spread_bps": str(i),
                }
                for i in range(70)
            ]
            pd.DataFrame(rows).to_csv(path, index=False)
            with self.assertRaisesRegex(RuntimeError, "no eligible baseline"):
                load_model_state(path, "2Y", start + timedelta(days=100))

    def test_daily_observation_preserves_same_row_components(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.csv"
            pd.DataFrame(
                [{
                    "timestamp_utc": "2026-08-28T21:00:00+00:00",
                    "maturity": "2Y",
                    "strategy_version": LIVE_SIGNAL_STRATEGY_VERSION,
                    "eris_rate_bps": "378.5",
                    "fred_series": "DGS2",
                    "treasury_rate_bps": "362.0",
                    "spread_bps": "16.5",
                }]
            ).to_csv(path, index=False)
            row = load_daily_observation(
                path, "2Y", datetime(2026, 8, 29, tzinfo=timezone.utc)
            )
            self.assertEqual(row.spread_bps, Decimal("16.5"))
            self.assertEqual(row.fred_series, "DGS2")

    def test_signal_state_round_trip_is_atomic_and_exact(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "signal_state.json"
            state = {"2Y": -1, "5Y": 1}
            save_signal_state(path, state)
            self.assertEqual(load_signal_state(path), state)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())
            self.assertEqual(json.loads(path.read_text()), state)

    def test_missing_signal_state_returns_flat_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            self.assertEqual(load_signal_state(path), {"2Y": 0, "5Y": 0})


if __name__ == "__main__":
    unittest.main()
