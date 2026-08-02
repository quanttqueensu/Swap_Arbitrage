from __future__ import annotations

import csv
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from data_pipeline.contracts import SCHEMAS, validate_csv
from data_pipeline.ibkr_paper_source import (
    IbkrPaperRecorder,
    PaperSafetyError,
    PaperSessionConfig,
)
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
        for sensitive in ("DU12345678", "client_id=42", "credential=not-for-csv", "localhost", "127.0.0.1", "paper-gateway", "node-01", "127.0.0.1:7497"):
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

    def test_accepts_unrestricted_order_status_containing_broker_and_gateway(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        row = {
            "order_ref": "order-1", "decision_id": "decision-1", "created_at_utc": "2026-08-02T15:00:00Z",
            "instrument_id": "IBKR:1", "side": "BUY", "quantity": 2, "order_type": "MKT",
            "time_in_force": "DAY", "status": "broker gateway acknowledged", "ibkr_order_id": "",
        }
        self.assertEqual(store.write("paper_orders", [row]), 1)
        self.assertIn("broker gateway acknowledged", store.path_for("paper_orders").read_text(encoding="utf-8"))

    def test_rejects_exact_endpoint_in_unrestricted_order_status(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        for endpoint in ("127.0.0.1:7497", "localhost:7497", "node-01:7497"):
            with self.subTest(endpoint=endpoint):
                row = {
                    "order_ref": "order-1", "decision_id": "decision-1", "created_at_utc": "2026-08-02T15:00:00Z",
                    "instrument_id": "IBKR:1", "side": "BUY", "quantity": 2, "order_type": "MKT",
                    "time_in_force": "DAY", "status": endpoint, "ibkr_order_id": "",
                }
                with self.assertRaisesRegex(ValueError, "sensitive") as caught:
                    store.write("paper_orders", [row])
                self.assertNotIn(endpoint, str(caught.exception))

    def test_accepts_ambiguous_bare_host_like_order_reference(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        row = {
            "order_ref": "node-01", "decision_id": "decision-1", "created_at_utc": "2026-08-02T15:00:00Z",
            "instrument_id": "IBKR:1", "side": "BUY", "quantity": 2, "order_type": "MKT",
            "time_in_force": "DAY", "status": "planned", "ibkr_order_id": "",
        }
        self.assertEqual(store.write("paper_orders", [row]), 1)

    def test_replace_failure_preserves_existing_destination(self) -> None:
        store = PaperEventStore(self.root, "agent_0", "run-1")
        store.write("paper_quotes", [quote("2026-08-02T15:00:00Z", "IBKR:1", "98", "99")])
        path = store.path_for("paper_quotes")
        before = path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                store.write("paper_quotes", [quote("2026-08-02T15:00:01Z", "IBKR:2", "99", "100")])
        self.assertEqual(path.read_bytes(), before)


class FakeIB:
    def __init__(self, *, connected: bool = True, accounts: tuple[str, ...] = ("DU12345678",)) -> None:
        self.connected = connected
        self.accounts = accounts
        self.market_data_requests: list[tuple[object, str, bool, bool]] = []
        self.is_connected_calls = 0
        self.managed_account_calls = 0

    def isConnected(self) -> bool:
        self.is_connected_calls += 1
        return self.connected

    def managedAccounts(self) -> tuple[str, ...]:
        self.managed_account_calls += 1
        return self.accounts

    def reqMktData(
        self, contract: object, generic_tick_list: str, snapshot: bool, regulatory_snapshot: bool
    ) -> SimpleNamespace:
        self.market_data_requests.append((contract, generic_tick_list, snapshot, regulatory_snapshot))
        return SimpleNamespace()


class PaperRecorderQuoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = PaperEventStore(Path(self.tempdir.name), "agent_0", "run-1")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def recorder_for(self, **changes: object) -> IbkrPaperRecorder:
        clock = changes.pop("clock", lambda: datetime.now(timezone.utc))
        values: dict[str, object] = {
            "host": "127.0.0.1",
            "port": 7497,
            "client_id": 30,
            "account_id": "DU12345678",
            "account_alias": "paper-primary",
        }
        values.update(changes)
        return IbkrPaperRecorder(FakeIB(), PaperSessionConfig(**values), self.store, clock)

    @staticmethod
    def contract(con_id: int) -> SimpleNamespace:
        return SimpleNamespace(conId=con_id)

    def test_unsafe_configuration_blocks_before_broker_access(self) -> None:
        for change, message in (
            ({"host": "192.0.2.1"}, "paper host"),
            ({"port": 7496}, "paper port"),
            ({"client_id": 0}, "client ID"),
            ({"paper_only": False}, "paper-only"),
            ({"live_trading_enabled": True}, "live trading"),
            ({"account_id": "U12345678"}, "DU paper account"),
            ({"account_alias": ""}, "account alias"),
            ({"stale_after_seconds": 0}, "stale quote limit"),
        ):
            with self.subTest(change=change):
                recorder = self.recorder_for(**change)
                with self.assertRaisesRegex(PaperSafetyError, message) as caught:
                    recorder.request_quotes([self.contract(101)])
                self.assertNotIn(str(change.get("account_id", "DU12345678")), str(caught.exception))
                self.assertEqual(recorder.ib.is_connected_calls, 0)
                self.assertEqual(recorder.ib.managed_account_calls, 0)
                self.assertEqual(recorder.ib.market_data_requests, [])

    def test_disconnected_and_account_mismatch_block_quote_requests(self) -> None:
        disconnected = self.recorder_for()
        disconnected.ib.connected = False
        with self.assertRaisesRegex(PaperSafetyError, "not connected"):
            disconnected.request_quotes([self.contract(101)])
        self.assertEqual(disconnected.ib.market_data_requests, [])

        mismatched = self.recorder_for()
        mismatched.ib.accounts = ("DU87654321",)
        with self.assertRaisesRegex(PaperSafetyError, "managed account") as caught:
            mismatched.request_quotes([self.contract(101)])
        self.assertNotIn("DU12345678", str(caught.exception))
        self.assertNotIn("DU87654321", str(caught.exception))
        self.assertEqual(mismatched.ib.market_data_requests, [])

    def test_request_quotes_requires_positive_stable_ids_and_returns_tickers(self) -> None:
        recorder = self.recorder_for()
        first, second = self.contract(101), self.contract(202)
        tickers = recorder.request_quotes([first, second])
        self.assertEqual(len(tickers), 2)
        self.assertEqual(
            recorder.ib.market_data_requests,
            [(first, "", False, False), (second, "", False, False)],
        )
        for invalid in (self.contract(0), self.contract(-1), SimpleNamespace(conId="101"), SimpleNamespace()):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(PaperSafetyError, "positive contract ID"):
                    recorder.request_quotes([invalid])

    def test_record_quote_normalizes_utc_and_writes_stable_instrument_id(self) -> None:
        recorder = self.recorder_for()
        ticker = SimpleNamespace(bid="99.25", ask="99.5", bidSize="10", askSize="12")
        observed = datetime(2026, 8, 2, 7, 0, tzinfo=timezone(timedelta(hours=-4)))
        recorder = self.recorder_for(clock=lambda: observed.astimezone(timezone.utc))
        count = recorder.record_quote(self.contract(101), ticker, observed)
        self.assertEqual(count, 1)
        self.assertEqual(
            self.store.path_for("paper_quotes").read_text(encoding="utf-8"),
            "timestamp_utc,instrument_id,bid_price,ask_price,bid_size,ask_size\n"
            "2026-08-02T11:00:00Z,IBKR:101,99.25,99.5,10,12\n",
        )

    def test_record_quote_rejects_stale_future_nan_nonpositive_and_crossed_values(self) -> None:
        now = datetime(2026, 8, 2, 15, 0, tzinfo=timezone.utc)
        recorder = self.recorder_for(stale_after_seconds=30, clock=lambda: now)
        cases = (
            (now - timedelta(seconds=31), SimpleNamespace(bid=99, ask=100, bidSize=1, askSize=1), "stale"),
            (datetime(2026, 8, 2, 15, 0, 1, tzinfo=timezone.utc), SimpleNamespace(bid=99, ask=100, bidSize=1, askSize=1), "stale"),
            (now, SimpleNamespace(bid=float("nan"), ask=100, bidSize=1, askSize=1), "finite"),
            (now, SimpleNamespace(bid=0, ask=100, bidSize=1, askSize=1), "positive"),
            (now, SimpleNamespace(bid=101, ask=100, bidSize=1, askSize=1), "crossed"),
        )
        for observed, ticker, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(PaperSafetyError, message):
                    recorder.record_quote(self.contract(101), ticker, observed)


if __name__ == "__main__":
    unittest.main()
