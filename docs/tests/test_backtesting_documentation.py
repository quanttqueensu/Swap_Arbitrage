"""Guard the single maintained backtesting workflow in user-facing docs."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
MAINTAINED_DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "TECHNICAL_DOCUMENTATION.md",
    ROOT / "docs" / "FILE_MAP.md",
    ROOT / "docs" / "FUNCTION_INVENTORY.md",
)


class BacktestingDocumentationTests(unittest.TestCase):
    def test_documented_workflow_uses_only_the_canonical_cli(self) -> None:
        documents = {path.name: path.read_text(encoding="utf-8") for path in MAINTAINED_DOCS}

        for path, text in documents.items():
            with self.subTest(path=path):
                self.assertNotIn("python " + "backtest_engine.py", text)
                self.assertNotIn("backtest_" + "engine.py", text)
                self.assertNotIn("legacy " + "backtest", text)
                self.assertIn("python -m backtesting", text)

        self.assertIn("run_historical_backtest", documents["FUNCTION_INVENTORY.md"])
        self.assertIn("backtesting/historical.py", documents["FILE_MAP.md"])
        technical = " ".join(documents["TECHNICAL_DOCUMENTATION.md"].split())
        self.assertIn(
            "backtesting.historical.run_historical_backtest",
            technical,
        )
        self.assertIn(
            "Synthetic ReplayEvent fixtures are test mechanics",
            technical,
        )
        for path, text in documents.items():
            with self.subTest(output_path=path):
                self.assertIn("data/results/backtests/<run-id>/", text)


if __name__ == "__main__":
    unittest.main()
