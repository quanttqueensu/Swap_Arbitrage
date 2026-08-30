from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace

from agents.agent_1.risk_adapter import RuntimeRiskState, evaluate_runtime_risk


class RiskAdapterTests(unittest.TestCase):
    def test_maps_runtime_state_and_agent_limits_to_existing_risk_interface(self) -> None:
        state = RuntimeRiskState(
            capacity_scale=Decimal("1"),
            has_open_position=True,
            emergency_flatten=False,
            scheduled_flatten=False,
            data_fresh=True,
            bid_ask_valid=True,
            market_fields_valid=True,
            broker_connected=True,
            reconciled=True,
            roll_allowed=True,
            margin_reserve_ok=True,
            residual_fraction=Decimal("0.01"),
            portfolio_gross_dv01_usd_per_bp=Decimal("6000"),
            portfolio_net_dv01_usd_per_bp=Decimal("50"),
            orders_submitted=3,
            working_orders=1,
            session_pnl_usd=Decimal("25"),
            drawdown_usd=Decimal("10"),
        )
        config = SimpleNamespace(
            max_residual_dv01_fraction=Decimal("0.05"),
            max_gross_dv01=Decimal("10000"),
            max_net_dv01=Decimal("250"),
            max_order_groups_per_session=50,
            max_working_order_groups=2,
            max_session_loss_usd=Decimal("1000"),
            max_drawdown_usd=Decimal("1000"),
        )
        captured = {}

        def evaluator(**kwargs):
            captured.update(kwargs)
            return "decision"

        decision = evaluate_runtime_risk(state, config, evaluator=evaluator)

        self.assertEqual(decision, "decision")
        self.assertEqual(captured["max_residual_fraction"], Decimal("0.05"))
        self.assertEqual(captured["max_portfolio_gross_dv01_usd_per_bp"], Decimal("10000"))
        self.assertEqual(captured["max_portfolio_net_dv01_usd_per_bp"], Decimal("250"))
        self.assertEqual(captured["max_orders"], 50)
        self.assertEqual(captured["max_working_orders"], 2)
        self.assertEqual(captured["working_orders"], 1)

    def test_invalid_existing_risk_result_is_treated_as_adapter_failure(self) -> None:
        state = RuntimeRiskState.safe_defaults()
        config = SimpleNamespace(
            max_residual_dv01_fraction=Decimal("0.05"),
            max_gross_dv01=Decimal("10000"),
            max_net_dv01=Decimal("250"),
            max_order_groups_per_session=50,
            max_working_order_groups=2,
            max_session_loss_usd=Decimal("1000"),
            max_drawdown_usd=Decimal("1000"),
        )

        self.assertIsNone(
            evaluate_runtime_risk(state, config, evaluator=lambda **_: None)
        )


if __name__ == "__main__":
    unittest.main()
