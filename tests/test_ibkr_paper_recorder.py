from __future__ import annotations

import csv
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_pipeline.contracts import SCHEMAS, validate_csv
from data_pipeline.paper_store import PaperEventStore


def quote(timestamp_utc: str, instrument_id: str, bid_price: str, ask_price: str) -> dict[str, object]:
    return {
        "timestamp_utc": timestamp_utc,
        "instrument_id": instrument_id,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "bid_size": "10",
        "ask_size": "12",
    }


class PaperEventStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.socket_guard = patch.object(socket, "socket", side_effect=AssertionError("network access is forbidden"))
        self.socket_guard.start()

    def tearDown(self) -> None:
        self.socket_guard.stop()
        self.tempdir.cleanup()

    def test_store_merges_idempotently_and_sorts(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        rows = [
            quote("2026-08-02T15:00:01Z", "IBKR:2", "99", "100"),
            quote("2026-08-02T15:00:00Z", "IBKR:1", "98", "99"),
        ]
        self.assertEqual(store.write("paper_quotes", rows), 2)
        self.assertEqual(store.write("paper_quotes", [rows[0]]), 2)
        path = store.path_for("paper_quotes")
        self.assertEqual(validate_csv(SCHEMAS["paper_quotes"], path), 2)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            "timestamp_utc,instrument_id,bid_price,ask_price,bid_size,ask_size\n"
            "2026-08-02T15:00:00Z,IBKR:1,98,99,10,12\n"
            "2026-08-02T15:00:01Z,IBKR:2,99,100,10,12\n",
        )

    def test_rejects_parent_traversal_in_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid run_id"):
            PaperEventStore(self.root, "agent_0", "../run")

    def test_rejects_conflicting_duplicate_key(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        store.write("paper_quotes", [quote("2026-08-02T15:00:00Z", "IBKR:1", "98", "99")])
        with self.assertRaisesRegex(ValueError, "conflicting duplicate key"):
            store.write("paper_quotes", [quote("2026-08-02T15:00:00Z", "IBKR:1", "97", "99")])

    def test_rejects_unsupported_schema(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        with self.assertRaisesRegex(ValueError, "unsupported schema"):
            store.write("paper_decisions", [])

    def test_replace_failure_preserves_existing_destination(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        store.write("paper_quotes", [quote("2026-08-02T15:00:00Z", "IBKR:1", "98", "99")])
        path = store.path_for("paper_quotes")
        before = path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                store.write("paper_quotes", [quote("2026-08-02T15:00:01Z", "IBKR:2", "99", "100")])
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
