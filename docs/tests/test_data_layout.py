from __future__ import annotations

import importlib
import unittest
from pathlib import Path

from config import (
    CME_SWAP_DATA_FILE,
    DATA_DIR,
    RAW_PRICE_DATA_FILE,
    RATES_FILE,
    RISK_DATA_FILE,
    SIGNAL_DATA_FILE,
    SWAP_RATES_FILE,
    TREASURY_FUTURES_DATA_FILE,
    TREASURY_FUTURES_FILE,
)
from data_pipeline.contracts import SCHEMAS


class DataLayoutTests(unittest.TestCase):
    def test_raw_inputs_use_the_raw_data_folder(self) -> None:
        for path in (
            RATES_FILE,
            SWAP_RATES_FILE,
            CME_SWAP_DATA_FILE,
            TREASURY_FUTURES_FILE,
            TREASURY_FUTURES_DATA_FILE,
            RAW_PRICE_DATA_FILE,
            SIGNAL_DATA_FILE,
            RISK_DATA_FILE,
        ):
            with self.subTest(path=path.name):
                self.assertEqual(path.parent, DATA_DIR / "raw_data")

    def test_canonical_contracts_use_flat_named_folders(self) -> None:
        self.assertEqual(SCHEMAS["historical_rates"].path_pattern, "data/rates/rates_YYYY.csv")
        self.assertEqual(SCHEMAS["historical_futures_settlements"].path_pattern, "data/futures/futures_settlements_YYYY.csv")
        self.assertEqual(SCHEMAS["contract_risk"].path_pattern, "data/contract_risk/contract_risk_YYYY.csv")
        self.assertEqual(SCHEMAS["daily_market"].path_pattern, "data/market/daily_market_YYYY.csv")

    def test_manifest_and_staging_subsystems_are_removed(self) -> None:
        self.assertFalse((DATA_DIR / "manifests").exists())
        self.assertFalse((DATA_DIR / "staging").exists())
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("data_pipeline.manifests")
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("data_pipeline.migration")
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("data_pipeline.canonicalize")
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("data_pipeline.ibkr_paper_source")
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("data_pipeline.paper_store")

    def test_data_has_only_canonical_and_generated_folders(self) -> None:
        folders = {path.name for path in DATA_DIR.iterdir() if path.is_dir()}
        generated = {"paper", "results", "live_signal"}
        self.assertEqual(
            folders - generated,
            {"raw_data", "futures", "rates", "market", "contract_risk"},
        )

    def test_repository_support_files_use_the_approved_baskets(self) -> None:
        self.assertTrue((Path("docs") / "tests").is_dir())
        self.assertTrue((Path("docs") / "tools" / "data_audit.py").is_file())
        self.assertTrue((Path("agents") / "agent_0" / "tests" / "test_characterization.py").is_file())
        self.assertTrue((Path("data_pipeline") / "historical_data" / "historical_data_builder.py").is_file())
        self.assertFalse(Path("tests").exists())
        self.assertFalse((Path("tools") / "data_audit.py").exists())
        for legacy_name in ("backtest.py", "data_io.py", "raw_price_data.py", "risk_data.py", "signal_data.py"):
            with self.subTest(legacy_name=legacy_name):
                self.assertFalse(Path(legacy_name).exists())
        self.assertTrue(importlib.import_module("data_pipeline.historical_data.canonicalize"))
        self.assertTrue(importlib.import_module("data_pipeline.historical_data.historical_data_builder"))
        self.assertTrue(importlib.import_module("data_pipeline.live_data_pipeline.paper_store"))
        self.assertTrue(importlib.import_module("data_pipeline.live_data_pipeline.ibkr_paper_source"))


if __name__ == "__main__":
    unittest.main()
