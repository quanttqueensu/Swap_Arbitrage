from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from data_pipeline.live_data_pipeline.market_snapshot import publish_market_snapshot


class MarketSnapshotTests(unittest.TestCase):
    def test_publishes_atomic_current_curve_and_spread_artifacts(self) -> None:
        raw = pd.DataFrame(
            {
                "date": ["2026-08-28", "2026-08-31"],
                "dgs1mo": [3.7, 3.8],
                "dgs2": [4.0, 4.1],
                "dgs5": [4.2, 4.3],
                "dgs10": [4.4, 4.5],
                "dgs30": [4.8, 4.9],
            }
        )
        signals = pd.DataFrame(
            {
                "date": ["2026-08-28", "2026-08-31"],
                "eris_swap_2y_equivalent_par_rate_bps": [412, 415],
                "treasury_rate_proxy_bps_2y": [401, 410],
                "swap_spread_bps_2y": [11, 5],
                "swap_spread_bps_2y_z": [1.2, 0.5],
                "proxy_position_2y": [-1, 0],
                "eris_swap_5y_equivalent_par_rate_bps": [430, 440],
                "treasury_rate_proxy_bps_5y": [420, 430],
                "swap_spread_bps_5y": [10, 10],
                "swap_spread_bps_5y_z": [0.8, 0.9],
                "proxy_position_5y": [0, 0],
            }
        )

        with TemporaryDirectory() as temporary:
            paths = publish_market_snapshot(raw, signals, Path(temporary))

            self.assertTrue(all(path.exists() for path in paths))
            curve = json.loads(paths[0].read_text(encoding="utf-8"))
            spreads = json.loads(paths[2].read_text(encoding="utf-8"))
            self.assertEqual(curve["observed_date"], "2026-08-31")
            self.assertEqual(curve["nodes"][-1], {"tenor": "30Y", "years": 30.0, "yield_pct": 4.9})
            self.assertEqual(spreads["spreads"][0]["spread_bps"], 5.0)
            self.assertEqual(paths[1].read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(paths[3].read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertFalse(list(Path(temporary).glob("*.tmp")))
