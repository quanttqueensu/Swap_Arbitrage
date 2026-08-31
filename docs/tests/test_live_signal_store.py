from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from data_pipeline.live_data_pipeline.live_signal_store import append_rows


class LiveSignalStoreTests(unittest.TestCase):
    def test_invalid_row_is_rejected_before_creating_partial_audit(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_signals.csv"
            with self.assertRaisesRegex(ValueError, "unknown live-signal audit fields"):
                append_rows(path, [{"unknown": "value"}])
            self.assertFalse(path.exists())

    def test_existing_incompatible_header_is_not_appended(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_signals.csv"
            path.write_text("wrong,header\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match schema"):
                append_rows(path, [{}])
            self.assertEqual(path.read_text(encoding="utf-8"), "wrong,header\n")


if __name__ == "__main__":
    unittest.main()
