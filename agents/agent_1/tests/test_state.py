from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from agents.agent_1.state import AgentState, StateError, load_state, roll_session, save_state


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "state.json"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_round_trip_preserves_recovery_state(self) -> None:
        state = AgentState(
            target_version="2026-08-31:abc",
            bound_contracts={"2Y:swap": 123, "2Y:treasury": 456},
            submitted_order_refs=("A1:2Y:g1:SWAP",),
            last_successful_broker_snapshot={"positions": {"123": 2}},
            session_order_groups=3,
            session_pnl_date="2026-08-31",
            session_peak_pnl_usd=Decimal("125.50"),
        )

        save_state(self.path, state)
        loaded = load_state(self.path)

        self.assertEqual(loaded, state)
        self.assertFalse(self.path.with_name("state.json.tmp").exists())


    def test_session_roll_resets_rate_limit_but_preserves_group_sequence(self) -> None:
        state = AgentState(
            submitted_order_refs=("A1:2Y:abc:0042:SWAP",),
            submitted_order_ids={"A1:2Y:abc:0042:SWAP": 101},
            session_order_groups=49,
            next_group_sequence=42,
            session_pnl_date="2026-08-30",
            session_peak_pnl_usd=Decimal("250"),
        )

        rolled = roll_session(state, "2026-08-31")

        self.assertEqual(rolled.session_order_groups, 0)
        self.assertEqual(rolled.next_group_sequence, 42)
        self.assertEqual(rolled.session_pnl_date, "2026-08-31")
        self.assertEqual(rolled.session_peak_pnl_usd, Decimal("0"))
        self.assertEqual(rolled.submitted_order_refs, ())
        self.assertEqual(rolled.submitted_order_ids, {})

    def test_legacy_state_infers_monotonic_group_sequence(self) -> None:
        self.path.write_text(
            json.dumps({
                "target_version": "2026-08-30:abc",
                "bound_contracts": {},
                "submitted_order_refs": ["A1:2Y:abc:0042:SWAP"],
                "submitted_order_ids": {"A1:2Y:abc:0042:SWAP": 101},
                "active_groups": {"A1:2Y:abc:0042": {}},
                "last_successful_broker_snapshot": {},
                "session_order_groups": 7,
                "session_pnl_date": "2026-08-30",
                "session_peak_pnl_usd": "0",
            }),
            encoding="utf-8",
        )

        loaded = load_state(self.path)

        self.assertEqual(loaded.session_order_groups, 7)
        self.assertEqual(loaded.next_group_sequence, 42)

    def test_missing_state_returns_empty_recovery_state(self) -> None:
        self.assertEqual(load_state(self.path), AgentState())

    def test_corrupt_state_fails_closed(self) -> None:
        self.path.write_text("{not-json", encoding="utf-8")

        with self.assertRaisesRegex(StateError, "state"):
            load_state(self.path)


if __name__ == "__main__":
    unittest.main()
