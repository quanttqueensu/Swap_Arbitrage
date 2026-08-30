from __future__ import annotations

import unittest

from agents.agent_1.run import build_parser


class CliTests(unittest.TestCase):
    def test_exposes_exact_operator_commands(self):
        parser = build_parser()
        for command in ("run", "once", "status", "stop-and-flatten"):
            args = parser.parse_args([command])
            self.assertEqual(args.command, command)

    def test_requires_operator_command(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])


if __name__ == "__main__":
    unittest.main()


class AuditBoundaryTests(unittest.TestCase):
    def test_audit_failure_cancels_only_agent1_orders_and_raises(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from agents.agent_1.paper_audit import PaperAuditError
        from agents.agent_1.run import _record_audit_or_cancel

        calls = []
        config = SimpleNamespace(account="DU123", client_id=31)
        result = SimpleNamespace(
            status=SimpleNamespace(bindings={"2Y:swap": object()}),
            execution=SimpleNamespace(
                state=SimpleNamespace(submitted_order_ids={"A1:2Y:x:0001:SWAP": 101})
            ),
        )

        def audit_recorder(*args, **kwargs):
            raise PaperAuditError("boom")

        def canceller(ib, *, account_id, client_id):
            calls.append((account_id, client_id))
            return (101,)

        with self.assertRaises(PaperAuditError):
            _record_audit_or_cancel(
                ib=object(), store=object(), config=config, result=result,
                observed_at=datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc),
                audit_recorder=audit_recorder, canceller=canceller,
            )
        self.assertEqual(calls, [("DU123", 31)])

    def test_audit_boundary_passes_current_submitted_ids_and_bindings(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from agents.agent_1.run import _record_audit_or_cancel

        captured = {}
        config = SimpleNamespace(account="DU123", client_id=31)
        bindings = {"2Y:swap": object()}
        submitted = {"A1:2Y:x:0001:SWAP": 101}
        result = SimpleNamespace(
            status=SimpleNamespace(bindings=bindings),
            execution=SimpleNamespace(state=SimpleNamespace(submitted_order_ids=submitted)),
        )
        observed = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)

        def audit_recorder(ib, store, **kwargs):
            captured.update(kwargs)
            return {"quotes": 1, "positions": 0, "fills": 0}

        summary = _record_audit_or_cancel(
            ib=object(), store=object(), config=config, result=result,
            observed_at=observed, audit_recorder=audit_recorder,
            canceller=lambda *args, **kwargs: (),
        )
        self.assertEqual(summary["quotes"], 1)
        self.assertIs(captured["bindings"], bindings)
        self.assertIs(captured["submitted_order_ids"], submitted)
        self.assertEqual(captured["account_id"], "DU123")
        self.assertEqual(captured["observed_at"], observed)
