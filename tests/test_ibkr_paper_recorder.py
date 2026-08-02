from __future__ import annotations

import csv
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
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

    def test_rejects_dot_segment_agent_and_run_ids(self) -> None:
        for field, agent_id, run_id in (
            ("agent_id", ".", "run-1"),
            ("agent_id", "..", "run-1"),
            ("run_id", "agent_0", "."),
            ("run_id", "agent_0", ".."),
        ):
            with self.subTest(field=field, value=agent_id if field == "agent_id" else run_id):
                with self.assertRaisesRegex(ValueError, f"invalid {field}"):
                    PaperEventStore(self.root, agent_id, run_id)

    def test_rejects_conflicting_duplicate_key(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        store.write("paper_quotes", [quote("2026-08-02T15:00:00Z", "IBKR:1", "98", "99")])
        with self.assertRaisesRegex(ValueError, "conflicting duplicate key"):
            store.write("paper_quotes", [quote("2026-08-02T15:00:00Z", "IBKR:1", "97", "99")])

    def test_rejects_unsupported_schema(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        with self.assertRaisesRegex(ValueError, "unsupported schema"):
            store.write("paper_decisions", [])

    def test_order_status_is_the_only_reconcilable_order_field(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        planned = {
            "order_ref": "order-1", "decision_id": "decision-1", "created_at_utc": "2026-08-02T15:00:00Z",
            "instrument_id": "IBKR:1", "side": "BUY", "quantity": 2, "order_type": "MKT",
            "time_in_force": "DAY", "status": "planned", "ibkr_order_id": "",
        }
        submitted = {**planned, "status": "submitted"}
        self.assertEqual(store.write("paper_orders", [planned]), 1)
        self.assertEqual(store.write("paper_orders", [submitted]), 1)
        with self.assertRaisesRegex(ValueError, "conflicting duplicate key"):
            store.write("paper_orders", [{**submitted, "quantity": 3}])

    def test_serializes_datetime_decimal_and_mixed_fractional_utc_in_contract_order(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        rows = [
            {**quote("placeholder", "IBKR:2", "99", "100"), "timestamp_utc": datetime(2026, 8, 2, 15, 0, 0, 100000, tzinfo=timezone.utc), "bid_price": Decimal("99E+0")},
            {**quote("2026-08-02T15:00:00Z", "IBKR:1", "98", "99")},
        ]
        self.assertEqual(store.write("paper_quotes", rows), 2)
        self.assertEqual(
            store.path_for("paper_quotes").read_text(encoding="utf-8").splitlines()[1:3],
            [
                "2026-08-02T15:00:00Z,IBKR:1,98,99,10,12",
                "2026-08-02T15:00:00.100000Z,IBKR:2,99,100,10,12",
            ],
        )

    def test_rejects_sensitive_values_without_echoing_them(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        for sensitive in ("DU12345678", "client_id=42", "credential=not-for-csv", "localhost", "127.0.0.1", "paper-gateway"):
            with self.subTest(sensitive=sensitive):
                with self.assertRaisesRegex(ValueError, "sensitive") as caught:
                    store.write("paper_quotes", [quote("2026-08-02T15:00:00Z", sensitive, "98", "99")])
                self.assertNotIn(sensitive, str(caught.exception))

    def test_conflicting_duplicate_error_redacts_the_key_value(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        private_key = "IBKR:private-order-marker"
        store.write("paper_quotes", [quote("2026-08-02T15:00:00Z", private_key, "98", "99")])
        with self.assertRaisesRegex(ValueError, "conflicting duplicate key") as caught:
            store.write("paper_quotes", [quote("2026-08-02T15:00:00Z", private_key, "97", "99")])
        self.assertNotIn(private_key, str(caught.exception))

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
