from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents.agent_1.run import build_parser, main


class CliTests(unittest.TestCase):
    def test_exposes_exact_operator_commands(self):
        parser = build_parser()
        for command in (
            "run",
            "once",
            "status",
            "delayed-status",
            "delayed-run",
            "delayed-once",
            "stop-and-flatten",
        ):
            arguments = [command]
            args = parser.parse_args(arguments)
            self.assertEqual(args.command, command)

    def test_delayed_commands_do_not_accept_a_target_mode_flag(self):
        parser = build_parser()
        for command in ("delayed-once", "delayed-run"):
            with self.subTest(command=command), self.assertRaises(SystemExit):
                parser.parse_args([command, "--legacy-target"])

    def test_requires_operator_command(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])


if __name__ == "__main__":
    unittest.main()


class LiveRunSelectionTests(unittest.TestCase):
    def test_run_uses_auto_live_target_by_default(self):
        broker = SimpleNamespace(sleep=lambda seconds: None)
        config = SimpleNamespace()
        provider = object()
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
                "agents.agent_1.run.build_live_target_provider",
                return_value=provider,
            ) as build,
            patch("agents.agent_1.run._create_store", return_value=object()),
            patch("agents.agent_1.run.polling_loop") as loop,
        ):
            result = main(["run", "--run-id", "auto-live-test"])

        self.assertEqual(result, 0)
        self.assertEqual(build.call_args.kwargs["held_contracts"], {})
        loop.assert_called_once()

    def test_status_reports_missing_contract_risk_without_a_traceback(self):
        from io import StringIO
        from agents.agent_1.risk import ContractRiskError

        broker = SimpleNamespace()
        config = SimpleNamespace(min_days_to_expiry=14)
        output = StringIO()
        with (
            patch("agents.agent_1.run.load_config", return_value=config),
            patch("agents.agent_1.run._load_evaluator", return_value=object()),
            patch("agents.agent_1.run.connect_paper", return_value=broker),
            patch("agents.agent_1.run.disconnect"),
            patch("agents.agent_1.run.load_state", return_value=SimpleNamespace(bound_contracts={})),
            patch("agents.agent_1.run.build_live_target_provider", return_value=object()),
            patch(
                "agents.agent_1.run.status_cycle",
                side_effect=ContractRiskError("Contract-risk source does not exist"),
            ),
            patch("sys.stdout", output),
        ):
            result = main(["status"])

        self.assertEqual(result, 2)
        self.assertIn("action=hold", output.getvalue())
        self.assertIn("contract-risk-unavailable", output.getvalue())

    def test_delayed_status_reports_missing_account_risk_without_a_traceback(self):
        from io import StringIO
        from agents.agent_1.risk import AccountRiskError

        broker = SimpleNamespace()
        config = SimpleNamespace(min_days_to_expiry=14)
        output = StringIO()
        with (
            patch("agents.agent_1.run.load_config", return_value=config),
            patch("agents.agent_1.run._load_evaluator", return_value=object()),
            patch("agents.agent_1.run.connect_paper", return_value=broker),
            patch("agents.agent_1.run.disconnect"),
            patch("agents.agent_1.run.request_delayed_market_data"),
            patch("agents.agent_1.run.load_state", return_value=SimpleNamespace(bound_contracts={})),
            patch("agents.agent_1.run.build_live_target_provider", return_value=object()),
            patch(
                "agents.agent_1.run.status_cycle",
                side_effect=AccountRiskError("Invalid IBKR daily P&L."),
            ),
            patch("sys.stdout", output),
        ):
            result = main(["delayed-status"])

        self.assertEqual(result, 2)
        self.assertIn("action=hold", output.getvalue())
        self.assertIn("account-risk-unavailable", output.getvalue())

    def test_delayed_status_requests_delayed_data_and_never_builds_an_engine(self):
        from io import StringIO
        from agents.agent_1.risk import ContractRiskError

        broker = SimpleNamespace()
        config = SimpleNamespace(min_days_to_expiry=14)
        output = StringIO()
        with (
            patch("agents.agent_1.run.load_config", return_value=config),
            patch("agents.agent_1.run._load_evaluator", return_value=object()),
            patch("agents.agent_1.run.connect_paper", return_value=broker),
            patch("agents.agent_1.run.disconnect"),
            patch("agents.agent_1.run.request_delayed_market_data") as delayed,
            patch("agents.agent_1.run.load_state", return_value=SimpleNamespace(bound_contracts={})),
            patch("agents.agent_1.run.build_live_target_provider", return_value=object()),
            patch(
                "agents.agent_1.run.status_cycle",
                side_effect=ContractRiskError("Contract-risk source does not exist"),
            ),
            patch("agents.agent_1.run.Agent1Engine") as engine,
            patch("sys.stdout", output),
        ):
            result = main(["delayed-status"])

        self.assertEqual(result, 2)
        delayed.assert_called_once_with(broker)
        engine.assert_not_called()

    def test_delayed_run_uses_delayed_data_and_legacy_target(self):
        from datetime import datetime, timezone

        broker = SimpleNamespace(sleep=lambda seconds: None)
        config = SimpleNamespace()
        engine = SimpleNamespace(cycle=Mock(return_value=SimpleNamespace(status=object())))
        cycle_now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)

        def run_one_cycle(**kwargs):
            kwargs["cycle"](cycle_now)

        with (
            patch("agents.agent_1.run.load_config", return_value=config),
            patch("agents.agent_1.run._load_evaluator", return_value=object()),
            patch("agents.agent_1.run.connect_paper", return_value=broker),
            patch("agents.agent_1.run.disconnect"),
            patch("agents.agent_1.run.request_delayed_market_data") as delayed,
            patch("agents.agent_1.run._refresh_delayed_target") as refresh,
            patch("agents.agent_1.run._stop_requested", return_value=False),
            patch("agents.agent_1.run.build_live_target_provider") as build,
            patch("agents.agent_1.run._create_store", return_value=object()),
            patch("agents.agent_1.run.Agent1Engine", return_value=engine),
            patch("agents.agent_1.run._record_audit_or_cancel"),
            patch("agents.agent_1.run._render_status", return_value="status"),
            patch("agents.agent_1.run.polling_loop", side_effect=run_one_cycle) as loop,
        ):
            result = main(["delayed-run", "--run-id", "delayed-test"])

        self.assertEqual(result, 0)
        delayed.assert_called_once_with(broker)
        refresh.assert_called_once()
        build.assert_not_called()
        loop.assert_called_once()
        engine.cycle.assert_called_once_with(cycle_now, stop_requested=False)

    def test_delayed_once_uses_delayed_data_and_legacy_target(self):
        broker = SimpleNamespace()
        config = SimpleNamespace()
        engine = SimpleNamespace(cycle=Mock(return_value=SimpleNamespace(status=object())))
        with (
            patch("agents.agent_1.run.load_config", return_value=config),
            patch("agents.agent_1.run._load_evaluator", return_value=object()),
            patch("agents.agent_1.run.connect_paper", return_value=broker),
            patch("agents.agent_1.run.disconnect"),
            patch("agents.agent_1.run.request_delayed_market_data") as delayed,
            patch("agents.agent_1.run._refresh_delayed_target") as refresh,
            patch("agents.agent_1.run._stop_requested", return_value=False),
            patch("agents.agent_1.run.build_live_target_provider") as build,
            patch("agents.agent_1.run._create_store", return_value=object()),
            patch("agents.agent_1.run.Agent1Engine", return_value=engine),
            patch("agents.agent_1.run._record_audit_or_cancel"),
            patch("agents.agent_1.run._render_status", return_value="status"),
        ):
            result = main(["delayed-once", "--run-id", "delayed-once-test"])

        self.assertEqual(result, 0)
        delayed.assert_called_once_with(broker)
        refresh.assert_called_once()
        build.assert_not_called()
        engine.cycle.assert_called_once()

    def test_delayed_target_refresh_rebuilds_only_the_default_target(self):
        from pathlib import Path
        from agents.agent_1.run import DEFAULT_TARGET_PATH, _refresh_delayed_target

        with patch("risk_pipeline.build_risk_data") as build:
            _refresh_delayed_target(DEFAULT_TARGET_PATH)
            _refresh_delayed_target(Path("custom-target.csv"))

        build.assert_called_once_with(
            refresh_signals=True,
            pull_interest_rates=True,
            pull_eris=True,
            save=True,
        )

    def test_delayed_target_refresh_failure_prevents_broker_connection(self):
        with (
            patch("agents.agent_1.run.load_config", return_value=SimpleNamespace()),
            patch("agents.agent_1.run._load_evaluator", return_value=object()),
            patch(
                "agents.agent_1.run._refresh_delayed_target",
                side_effect=RuntimeError("refresh failed"),
            ),
            patch("agents.agent_1.run.connect_paper") as connect,
        ):
            with self.assertRaisesRegex(RuntimeError, "refresh failed"):
                main(["delayed-once"])

        connect.assert_not_called()




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
            config = SimpleNamespace()
            engine = SimpleNamespace(cycle=Mock(return_value=SimpleNamespace(status=object())))
            cycle_now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)

            def run_one_cycle(**kwargs):
                kwargs["cycle"](cycle_now)

            with (
                patch("agents.agent_1.run.load_config", return_value=config),
                patch("agents.agent_1.run._load_evaluator", return_value=object()),
                patch("agents.agent_1.run.connect_paper", return_value=broker),
                patch("agents.agent_1.run.disconnect"),
                patch(
                    "agents.agent_1.run.load_state",
                    return_value=SimpleNamespace(bound_contracts={}),
                ),
                patch("agents.agent_1.run.build_live_target_provider", return_value=object()),
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
                    return_value=SimpleNamespace(),
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

    def test_stop_and_flatten_requests_delayed_quotes(self):
        result = SimpleNamespace(status=object(), execution=object())
        engine = SimpleNamespace(cycle=Mock(return_value=result))
        broker = SimpleNamespace()
        with (
            patch("agents.agent_1.run.load_config", return_value=SimpleNamespace()),
            patch("agents.agent_1.run._load_evaluator", return_value=object()),
            patch("agents.agent_1.run.connect_paper", return_value=broker),
            patch("agents.agent_1.run.disconnect"),
            patch("agents.agent_1.run.request_delayed_market_data") as delayed,
            patch("agents.agent_1.run.load_state", return_value=SimpleNamespace(bound_contracts={})),
            patch("agents.agent_1.run.build_live_target_provider", return_value=object()),
            patch("agents.agent_1.run._create_store", return_value=object()),
            patch("agents.agent_1.run.Agent1Engine", return_value=engine),
            patch("agents.agent_1.run._record_audit_or_cancel"),
            patch("agents.agent_1.run._render_status", return_value="status"),
            patch("agents.agent_1.run._is_flat", return_value=True),
        ):
            self.assertEqual(main(["stop-and-flatten"]), 0)
        delayed.assert_called_once_with(broker)


class AuditBoundaryTests(unittest.TestCase):
    def test_audit_failure_cancels_only_agent1_orders_and_raises(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from agents.agent_1.audit import PaperAuditError
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
