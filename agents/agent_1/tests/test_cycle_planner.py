from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from agents.agent_1.contract_risk import ContractRisk
from agents.agent_1.models import (
    BoundContract,
    BrokerSnapshot,
    DailyTarget,
    MaturityTarget,
    QuoteSnapshot,
    WorkingOrderSnapshot,
)
from agents.agent_1.supervisor import plan_cycle


def binding(maturity, leg, con_id, symbol, risk_id):
    return BoundContract(
        maturity=maturity,
        leg=leg,
        con_id=con_id,
        symbol=symbol,
        local_symbol=f"{symbol}X",
        min_tick=Decimal("0.01"),
        risk_id=risk_id,
    )


def risk(con_id, risk_id, dv01):
    return ContractRisk(
        con_id=con_id,
        risk_id=risk_id,
        observation_date=date(2026, 8, 31),
        dv01_usd_per_bp=Decimal(str(dv01)),
        rate_sensitivity_sign=-1,
        method="test",
    )


class CyclePlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
        self.bindings = {
            "2Y:swap": binding("2Y", "swap", 1, "YIT", "r1"),
            "2Y:treasury": binding("2Y", "treasury", 2, "ZT", "r2"),
            "5Y:swap": binding("5Y", "swap", 3, "YIW", "r3"),
            "5Y:treasury": binding("5Y", "treasury", 4, "ZF", "r4"),
        }
        self.risks = {
            1: risk(1, "r1", 20),
            2: risk(2, "r2", 40),
            3: risk(3, "r3", 40),
            4: risk(4, "r4", 40),
        }
        self.config = SimpleNamespace(
            max_quote_age_seconds=Decimal("10"),
            order_group_timeout_seconds=Decimal("30"),
            max_residual_dv01_fraction=Decimal("0.10"),
            max_gross_dv01=Decimal("10000"),
            max_net_dv01=Decimal("250"),
            max_order_groups_per_session=50,
            max_working_order_groups=2,
            max_session_loss_usd=Decimal("1000"),
            max_drawdown_usd=Decimal("1000"),
            max_2y_swap_contracts=100,
            max_2y_treasury_contracts=100,
            max_5y_swap_contracts=100,
            max_5y_treasury_contracts=100,
        )
        self.target = DailyTarget(
            as_of=date(2026, 8, 31),
            version="2026-08-31:abcdef0123456789",
            age_business_days=0,
            target_2y=MaturityTarget(10, -5),
            target_5y=MaturityTarget(0, 0),
        )

    def snapshot(self, positions=None, working=()):
        positions = positions or {1: 0, 2: 0, 3: 0, 4: 0}
        return BrokerSnapshot(
            observed_at=self.now,
            positions=positions,
            working_orders=tuple(working),
            quotes={
                con_id: QuoteSnapshot(con_id, Decimal("99"), Decimal("99.01"), self.now)
                for con_id in (1, 2, 3, 4)
            },
        )

    def allow(self, **kwargs):
        return SimpleNamespace(
            allowed=True,
            flatten_requested=False,
            reason_codes=("within_limits",),
        )

    def test_valid_target_builds_single_2y_group_and_no_5y_group(self) -> None:
        plan = plan_cycle(
            target=self.target,
            target_error=None,
            snapshot=self.snapshot(),
            bindings=self.bindings,
            risks=self.risks,
            config=self.config,
            now=self.now,
            session_order_groups=0,
            evaluator=self.allow,
        )
        self.assertEqual(plan.action, "trade")
        self.assertEqual([group.maturity for group in plan.groups], ["2Y"])
        self.assertEqual(plan.projected_dv01.net, Decimal("0"))

    def test_duplicate_poll_with_working_orders_at_target_creates_no_group(self) -> None:
        working = (
            WorkingOrderSnapshot("A1:2Y:g:SWAP", 1, 1, 2, "Submitted"),
            WorkingOrderSnapshot("A1:2Y:g:TREASURY", 2, 2, -1, "Submitted"),
        )
        plan = plan_cycle(
            target=self.target,
            target_error=None,
            snapshot=self.snapshot({1: 8, 2: -4, 3: 0, 4: 0}, working),
            bindings=self.bindings,
            risks=self.risks,
            config=self.config,
            now=self.now,
            session_order_groups=1,
            evaluator=self.allow,
        )
        self.assertEqual(plan.action, "hold")
        self.assertEqual(plan.groups, ())

    def test_risk_block_with_open_position_requests_flatten_plan(self) -> None:
        def block(**kwargs):
            return SimpleNamespace(
                allowed=False,
                flatten_requested=True,
                reason_codes=("session_loss_limit",),
            )

        plan = plan_cycle(
            target=self.target,
            target_error=None,
            snapshot=self.snapshot({1: 4, 2: -2, 3: 0, 4: 0}),
            bindings=self.bindings,
            risks=self.risks,
            config=self.config,
            now=self.now,
            session_order_groups=0,
            evaluator=block,
        )
        self.assertEqual(plan.action, "flatten")
        self.assertEqual(plan.reason_codes, ("session_loss_limit",))
        group = plan.groups[0]
        self.assertEqual((group.requested_swap_delta, group.requested_treasury_delta), (-4, 2))

    def test_invalid_target_flat_account_stays_flat_without_new_risk(self) -> None:
        plan = plan_cycle(
            target=None,
            target_error="stale target",
            snapshot=self.snapshot(),
            bindings=self.bindings,
            risks=self.risks,
            config=self.config,
            now=self.now,
            session_order_groups=0,
            evaluator=self.allow,
        )
        self.assertEqual(plan.action, "blocked")
        self.assertEqual(plan.groups, ())
        self.assertIn("target_invalid", plan.reason_codes)

    def test_contract_cap_blocks_new_exposure_before_risk_evaluator(self) -> None:
        self.config.max_2y_swap_contracts = 5
        calls = []
        plan = plan_cycle(
            target=self.target,
            target_error=None,
            snapshot=self.snapshot(),
            bindings=self.bindings,
            risks=self.risks,
            config=self.config,
            now=self.now,
            session_order_groups=0,
            evaluator=lambda **kwargs: calls.append(kwargs),
        )
        self.assertEqual(plan.action, "blocked")
        self.assertIn("contract_cap", plan.reason_codes)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
