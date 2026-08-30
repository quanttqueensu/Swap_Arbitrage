from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from agents.agent_1.state import AgentState, StateError, load_state, save_state


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

    def test_missing_state_returns_empty_recovery_state(self) -> None:
        self.assertEqual(load_state(self.path), AgentState())

    def test_corrupt_state_fails_closed(self) -> None:
        self.path.write_text("{not-json", encoding="utf-8")

        with self.assertRaisesRegex(StateError, "state"):
            load_state(self.path)


if __name__ == "__main__":
    unittest.main()
