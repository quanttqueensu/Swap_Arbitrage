from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents.agent_1.run import build_parser, main


class CliTests(unittest.TestCase):
    def test_exposes_exact_operator_commands(self):
        parser = build_parser()
        for command in ("run", "once", "status", "shadow-once", "stop-and-flatten"):
            arguments = [command]
            args = parser.parse_args(arguments)
            self.assertEqual(args.command, command)

    def test_daily_csv_is_default_and_live_target_is_explicit(self):
        parser = build_parser()
        default = parser.parse_args(["run"])
        self.assertFalse(default.live_target)
        self.assertFalse(default.legacy_target)
        self.assertTrue(parser.parse_args(["run", "--live-target"]).live_target)
        self.assertTrue(parser.parse_args(["run", "--legacy-target"]).legacy_target)

        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "--live-target", "--legacy-target"])

    def test_requires_operator_command(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])


if __name__ == "__main__":
    unittest.main()


class LiveRunSelectionTests(unittest.TestCase):
    def test_run_uses_daily_csv_target_by_default(self):
        broker = SimpleNamespace(sleep=lambda seconds: None)
        config = SimpleNamespace(live_target_enabled=False)
        with (
            patch("agents.agent_1.run.load_config", return_value=config),
            patch("agents.agent_1.run._load_evaluator", return_value=object()),
            patch("agents.agent_1.run.connect_paper", return_value=broker),
            patch("agents.agent_1.run.disconnect"),
            patch("agents.agent_1.run.build_auto_live_provider") as build,
            patch("agents.agent_1.run._create_store", return_value=object()),
            patch("agents.agent_1.run.polling_loop") as loop,
        ):
            result = main(["run", "--run-id", "daily-default-test"])

        self.assertEqual(result, 0)
        build.assert_not_called()
        loop.assert_called_once()

    def test_live_target_requires_config_and_cli_opt_in(self):
        broker = SimpleNamespace(sleep=lambda seconds: None)
        with patch(
            "agents.agent_1.run.load_config",
            return_value=SimpleNamespace(live_target_enabled=False),
        ):
            with self.assertRaisesRegex(RuntimeError, "live_target_enabled"):
                main(["run", "--live-target"])

        provider = object()
        config = SimpleNamespace(live_target_enabled=True)
        with (
            patch("agents.agent_1.run.load_config", return_value=config),
            patch("agents.agent_1.run._load_evaluator", return_value=object()),
            patch("agents.agent_1.run.connect_paper", return_value=broker),
            patch("agents.agent_1.run.disconnect"),
            patch(
                "agents.agent_1.run.load_state",
                return_value=SimpleNamespace(bound_contracts={}),
            ),
            patch(
                "agents.agent_1.run.build_auto_live_provider",
                return_value=provider,
            ) as build,
            patch("agents.agent_1.run._create_store", return_value=object()),
            patch("agents.agent_1.run.polling_loop") as loop,
        ):
            result = main(["run", "--live-target", "--run-id", "auto-live-test"])

        self.assertEqual(result, 0)
        self.assertTrue(build.call_args.kwargs["executable"])
        loop.assert_called_once()




class StopStateTests(unittest.TestCase):
    def test_stop_state_file_is_persistent_and_detectable(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from agents.agent_1.run import _request_stop, _stop_requested

        with TemporaryDirectory() as tmp:
            stop_path = Path(tmp) / "nested" / "STOP"
            self.assertFalse(_stop_requested(stop_path))
            _request_stop(stop_path)
            self.assertTrue(_stop_requested(stop_path))

    def test_run_threads_stop_file_state_into_each_engine_cycle(self):
        from datetime import datetime, timezone
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as tmp:
            stop_path = Path(tmp) / "STOP"
            stop_path.touch()
            broker = SimpleNamespace(sleep=lambda seconds: None)
            config = SimpleNamespace(live_target_enabled=False)
            engine = SimpleNamespace(cycle=Mock(return_value=SimpleNamespace(status=object())))
            cycle_now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)

            def run_one_cycle(**kwargs):
                kwargs["cycle"](cycle_now)

            with (
                patch("agents.agent_1.run.load_config", return_value=config),
                patch("agents.agent_1.run._load_evaluator", return_value=object()),
                patch("agents.agent_1.run.connect_paper", return_value=broker),
                patch("agents.agent_1.run.disconnect"),
                patch("agents.agent_1.run._create_store", return_value=object()),
                patch("agents.agent_1.run.Agent1Engine", return_value=engine),
                patch("agents.agent_1.run._record_audit_or_cancel"),
                patch("agents.agent_1.run._render_status", return_value="status"),
                patch("agents.agent_1.run.polling_loop", side_effect=run_one_cycle),
            ):
                result = main([
                    "run",
                    "--stop-file",
                    str(stop_path),
                    "--run-id",
                    "stop-state-test",
                ])

            self.assertEqual(result, 0)
            engine.cycle.assert_called_once_with(cycle_now, stop_requested=True)

    def test_stop_and_flatten_sets_stop_state_before_broker_connection(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as tmp:
            stop_path = Path(tmp) / "STOP"
            with (
                patch(
                    "agents.agent_1.run.load_config",
                    return_value=SimpleNamespace(live_target_enabled=False),
                ),
                patch("agents.agent_1.run._load_evaluator", return_value=object()),
                patch(
                    "agents.agent_1.run.connect_paper",
                    side_effect=RuntimeError("broker already connected"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "broker already connected"):
                    main(["stop-and-flatten", "--stop-file", str(stop_path)])

            self.assertTrue(stop_path.exists())


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
