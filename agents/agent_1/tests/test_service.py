from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal

from agents.agent_1.service import once_cycle, polling_loop
from agents.agent_1.active_groups import ActiveGroupResolution
from agents.agent_1.group_lifecycle import ActiveGroupDecision
from agents.agent_1.contract_risk import ContractRisk, PortfolioDV01
from agents.agent_1.models import BoundContract, BrokerSnapshot, MaturityReconciliation, PositionState, QuoteSnapshot
from agents.agent_1.order_groups import build_order_group, group_to_state
from agents.agent_1.supervisor import CyclePlan
from agents.agent_1.state import AgentState


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tempdir.name) / "state.json"
        self.config = SimpleNamespace(
            account="DU123", client_id=31, min_days_to_expiry=14,
            timezone="America/New_York", market_open_time=time(9), market_close_time=time(15),
            poll_interval_seconds=30,
        )
        self.now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_once_cycle_passes_stop_state_through_status_then_executor(self):
        calls = []
        status_result = SimpleNamespace(
            target="target", snapshot="snapshot", bindings="bindings", plan="plan"
        )
        execution_result = SimpleNamespace(state=AgentState())

        def status_runner(**kwargs):
            calls.append(("status", kwargs))
            return status_result

        def executor(**kwargs):
            calls.append(("execute", kwargs))
            return execution_result

        provider = object()
        result = once_cycle(
            ib=object(), config=self.config,
            target_path=Path("target.csv"), contract_risk_path=Path("risk.csv"),
            state_path=self.state_path, now=self.now, evaluator=lambda **_: None,
            stop_requested=True, status_runner=status_runner, executor=executor,
            target_provider=provider,
        )
        self.assertIs(result.status, status_result)
        self.assertIs(result.execution, execution_result)
        self.assertTrue(calls[0][1]["stop_requested"])
        self.assertEqual(calls[0][1]["min_days_to_expiry"], 14)
        self.assertIs(calls[0][1]["target_provider"], provider)
        self.assertEqual(calls[1][1]["plan"], "plan")

    def test_polling_loop_runs_cycles_only_inside_configured_market_window(self):
        times = iter([
            datetime(2026, 8, 31, 12, 59, tzinfo=timezone.utc),  # 08:59 NY
            datetime(2026, 8, 31, 13, 0, tzinfo=timezone.utc),   # 09:00 NY
            datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc),   # 15:00 NY
        ])
        runs = []
        sleeps = []
        polling_loop(
            config=self.config,
            cycle=lambda now: runs.append(now),
            now_fn=lambda: next(times),
            sleep_fn=lambda seconds: sleeps.append(seconds),
            max_iterations=3,
        )
        self.assertEqual(runs, [datetime(2026, 8, 31, 13, 0, tzinfo=timezone.utc)])
        self.assertEqual(sleeps, [30, 30, 30])

    def test_once_cycle_prioritizes_partial_fill_recovery_over_normal_target(self):
        bindings = {
            "2Y:swap": BoundContract("2Y", "swap", 1, "YIT", "YITM26", Decimal(".01"), "r1"),
            "2Y:treasury": BoundContract("2Y", "treasury", 2, "ZT", "ZTU26", Decimal(".01"), "r2"),
            "5Y:swap": BoundContract("5Y", "swap", 3, "YIW", "YIWM26", Decimal(".01"), "r3"),
            "5Y:treasury": BoundContract("5Y", "treasury", 4, "ZF", "ZFU26", Decimal(".01"), "r4"),
        }
        quotes = {
            "swap": QuoteSnapshot(1, Decimal("99"), Decimal("99.01"), self.now),
            "treasury": QuoteSnapshot(2, Decimal("100"), Decimal("100.01"), self.now),
        }
        group = build_order_group(
            maturity="2Y", target_version="2026-08-31:abc", sequence=1,
            reconciliation=MaturityReconciliation(10, -5, "expand"),
            swap_state=PositionState(0), treasury_state=PositionState(0),
            bindings={"swap": bindings["2Y:swap"], "treasury": bindings["2Y:treasury"]},
            quotes=quotes, created_at=self.now, timeout_seconds=Decimal("30"),
        )
        from agents.agent_1.state import save_state
        save_state(self.state_path, AgentState(active_groups={group.group_id: group_to_state(group)}))
        snapshot = BrokerSnapshot(self.now, {1: 6, 2: 0, 3: 0, 4: 0}, (), {
            1: quotes["swap"], 2: quotes["treasury"],
            3: QuoteSnapshot(3, Decimal("99"), Decimal("99.01"), self.now),
            4: QuoteSnapshot(4, Decimal("100"), Decimal("100.01"), self.now),
        })
        risks = {
            cid: ContractRisk(cid, f"r{cid}", self.now.date(), Decimal("20"), -1, "test")
            for cid in (1, 2, 3, 4)
        }
        normal_plan = CyclePlan(
            "trade", ("within_limits",), (), PortfolioDV01(Decimal("0"), Decimal("0"), Decimal("0")),
            {"2Y": MaturityReconciliation(4, -5, "expand")}, SimpleNamespace(allowed=True),
        )
        status_result = SimpleNamespace(
            target=None, target_error=None, bindings=bindings, snapshot=snapshot, risks=risks,
            recovery=SimpleNamespace(reconciled=True), margin_reserve_ok=True, plan=normal_plan,
        )
        captured = {}
        def planner(**kwargs):
            captured.setdefault("targets", []).append(kwargs["target"])
            return CyclePlan(
                "trade", ("within_limits",), (), PortfolioDV01(Decimal("0"), Decimal("0"), Decimal("0")),
                {}, SimpleNamespace(allowed=True),
            )
        def executor(**kwargs):
            captured["execute_target"] = kwargs["target"]
            captured["supersede"] = kwargs["supersede_group_ids"]
            return SimpleNamespace(state=kwargs["state"], submitted_order_ids={}, cancelled_order_ids=())

        once_cycle(
            ib=object(), config=SimpleNamespace(**self.config.__dict__, max_residual_dv01_fraction=Decimal("1"), margin_reserve_fraction=Decimal(".1")),
            target_path=Path("target.csv"), contract_risk_path=Path("risk.csv"), state_path=self.state_path,
            now=self.now, evaluator=lambda **_: SimpleNamespace(allowed=True),
            status_runner=lambda **_: status_result, executor=executor, planner=planner,
            margin_previewer=lambda **_: True,
        )
        recovery_target = captured["execute_target"]
        self.assertEqual(len(captured["targets"]), 1)
        self.assertEqual(recovery_target.target_2y.swap_qty, 6)
        self.assertEqual(recovery_target.target_2y.treasury_qty, -3)
        self.assertEqual(captured["supersede"], (group.group_id,))

    def test_operator_stop_allows_existing_flatten_group_to_work_until_lifecycle_intervenes(self):
        flatten_group_id = "A1:2Y:flatten:0001"
        flatten_group_state = {"target_version": "flatten:2026-08-31"}
        save = AgentState(active_groups={flatten_group_id: flatten_group_state})
        from agents.agent_1.state import save_state
        save_state(self.state_path, save)
        status_result = SimpleNamespace(
            target=None, bindings={}, snapshot=SimpleNamespace(), risks={},
            recovery=SimpleNamespace(reconciled=True), plan=SimpleNamespace(action="cancel_then_flatten", groups=()),
        )
        resolution = ActiveGroupResolution(
            decisions=(ActiveGroupDecision(flatten_group_id, "2Y", "wait"),),
            completed_group_ids=(), cancel_group_ids=(), wait_group_ids=(flatten_group_id,),
            supersede_group_ids=(), recovery_target=None,
        )
        executed = []
        result = once_cycle(
            ib=object(), config=SimpleNamespace(**self.config.__dict__, max_residual_dv01_fraction=Decimal(".1")),
            target_path=Path("target.csv"), contract_risk_path=Path("risk.csv"), state_path=self.state_path,
            now=self.now, evaluator=lambda **_: None, stop_requested=True,
            status_runner=lambda **_: status_result, active_resolver=lambda *args, **kwargs: resolution,
            executor=lambda **kwargs: executed.append(kwargs),
        )
        self.assertEqual(executed, [])
        self.assertEqual(result.active_resolution.wait_group_ids, (flatten_group_id,))

    def test_once_cycle_persists_successful_broker_snapshot_and_pnl_high_water_mark(self):
        snapshot = BrokerSnapshot(self.now, {1: 0}, (), {})
        status_result = SimpleNamespace(
            target=None, target_error="blocked", bindings={}, snapshot=snapshot, risks={},
            recovery=SimpleNamespace(reconciled=True), margin_reserve_ok=True,
            session_pnl_usd=Decimal("60"), session_peak_pnl_usd=Decimal("100"),
            drawdown_usd=Decimal("40"), session_pnl_date="2026-08-31",
            plan=SimpleNamespace(action="hold", groups=()),
        )
        captured = {}
        def executor(**kwargs):
            captured["state"] = kwargs["state"]
            return SimpleNamespace(state=kwargs["state"], submitted_order_ids={}, cancelled_order_ids=())
        once_cycle(
            ib=object(), config=self.config, target_path=Path("target.csv"),
            contract_risk_path=Path("risk.csv"), state_path=self.state_path, now=self.now,
            evaluator=lambda **_: None, status_runner=lambda **_: status_result, executor=executor,
        )
        from agents.agent_1.state import load_state
        saved = load_state(self.state_path)
        self.assertEqual(saved.session_pnl_date, "2026-08-31")
        self.assertEqual(saved.session_peak_pnl_usd, Decimal("100"))
        self.assertEqual(saved.last_successful_broker_snapshot["positions"], {"1": 0})
        self.assertEqual(captured["state"].session_peak_pnl_usd, Decimal("100"))


if __name__ == "__main__":
    unittest.main()
