from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from strategy import (
    TradeDirection,
    SpreadObservation,
    build_target_position,
    evaluate_risk,
    naive_cost,
    net_opportunity_bps,
    observed_cost,
    portfolio_dv01,
    rank_opportunities,
    select_portfolio_targets,
)


D = Decimal
DECISION = datetime(2026, 8, 3, 21, tzinfo=timezone.utc)


def observation(maturity, gross, cost_buffer, traditional_net, reverse_net, z_score):
    return SpreadObservation(
        maturity=maturity,
        observation_time_utc=DECISION,
        fixed_swap_spread_bps=D("0"),
        expected_funding_spread_bps=D("0"),
        gross_excess_spread_bps=gross,
        traditional_cost_buffer_bps=cost_buffer,
        reverse_cost_buffer_bps=cost_buffer,
        traditional_net_opportunity_bps=traditional_net,
        reverse_net_opportunity_bps=reverse_net,
        z_score=z_score,
        observation_count=252,
        source_quality_ok=True,
        is_fresh=True,
    )


def target(maturity, expected_cost_usd):
    return build_target_position(
        maturity=maturity,
        swap_instrument_id=f"YI{maturity}H27",
        treasury_instrument_id=f"ZT{maturity}H27",
        direction=TradeDirection.TRADITIONAL,
        base_target_dv01_usd_per_bp=D("1000"),
        decision_time_utc=DECISION,
        current_realized_vol=D("1"),
        prior_realized_vols=tuple(
            (DECISION - timedelta(days=63 - index), D("1"))
            for index in range(63)
        ),
        z_score=D("2"),
        swap_available_contracts=100,
        treasury_available_contracts=100,
        swap_dv01_usd_per_bp=D("100"),
        treasury_dv01_usd_per_bp=D("950"),
        current_swap_quantity_contracts=0,
        current_treasury_quantity_contracts=0,
        max_swap_contracts=0,
        max_treasury_contracts=0,
        available_gross_dv01_usd_per_bp=D("10000"),
        expected_cost_usd=expected_cost_usd,
    )


class P34StrategyFlowTests(unittest.TestCase):
    # Mutation caught: disconnecting an exported P34 component from the pure flow.
    def test_p34_pure_cost_to_allowed_portfolio_flow(self):
        two_year_cost = naive_cost(
            swap_bid_ask_usd=D("250"),
            treasury_bid_ask_usd=D("250"),
            commission_exchange_usd=D("100"),
            slippage_usd=D("200"),
            roll_close_usd=D("50"),
            roll_open_usd=D("50"),
            financing_not_in_funding_usd=D("100"),
            cost_base_dv01_usd_per_bp=D("1000"),
        )
        five_year_cost = observed_cost(
            swap_bid_ask_usd=D("600"),
            treasury_bid_ask_usd=D("600"),
            commission_exchange_usd=D("400"),
            slippage_usd=D("800"),
            roll_close_usd=D("200"),
            roll_open_usd=D("200"),
            financing_not_in_funding_usd=D("200"),
            cost_base_dv01_usd_per_bp=D("1000"),
        )
        self.assertIsNotNone(two_year_cost)
        self.assertIsNotNone(five_year_cost)
        self.assertEqual(
            (two_year_cost.total_cost_usd, two_year_cost.total_cost_bps),
            (D("1000"), D("1")),
        )
        self.assertEqual(
            (five_year_cost.total_cost_usd, five_year_cost.total_cost_bps),
            (D("3000"), D("3")),
        )
        two_year_net = net_opportunity_bps(
            TradeDirection.TRADITIONAL, D("25"), two_year_cost.total_cost_bps
        )
        five_year_net = net_opportunity_bps(
            TradeDirection.TRADITIONAL, D("15"), five_year_cost.total_cost_bps
        )
        two_year_reverse_net = net_opportunity_bps(
            TradeDirection.REVERSE, D("25"), two_year_cost.total_cost_bps
        )
        five_year_reverse_net = net_opportunity_bps(
            TradeDirection.REVERSE, D("15"), five_year_cost.total_cost_bps
        )
        self.assertEqual((two_year_net, five_year_net), (D("24"), D("12")))
        self.assertEqual((two_year_reverse_net, five_year_reverse_net), (D("-26"), D("-18")))

        two_year = target("2Y", two_year_cost.total_cost_usd)
        five_year = target("5Y", five_year_cost.total_cost_usd)
        self.assertIsNotNone(two_year)
        self.assertIsNotNone(five_year)
        self.assertEqual(two_year.expected_cost_usd, two_year_cost.total_cost_usd)
        self.assertEqual(five_year.expected_cost_usd, five_year_cost.total_cost_usd)
        observations = (
            observation(
                "2Y", D("25"), two_year_cost.total_cost_bps,
                two_year_net, two_year_reverse_net, D("2"),
            ),
            observation(
                "5Y", D("15"), five_year_cost.total_cost_bps,
                five_year_net, five_year_reverse_net, D("3"),
            ),
        )
        self.assertEqual(
            tuple(
                (
                    item.traditional_cost_buffer_bps,
                    item.reverse_cost_buffer_bps,
                    item.traditional_net_opportunity_bps,
                    item.reverse_net_opportunity_bps,
                )
                for item in observations
            ),
            (
                (D("1"), D("1"), D("24"), D("-26")),
                (D("3"), D("3"), D("12"), D("-18")),
            ),
        )
        ranks = rank_opportunities(observations)
        self.assertEqual(ranks, ("5Y", "2Y"))
        selected = select_portfolio_targets(ranks, (two_year, five_year), D("5000"), D("250"))
        self.assertEqual(selected, (five_year, two_year))
        gross, net = portfolio_dv01(selected)
        self.assertEqual((gross, net), (D("3900"), D("-100")))

        decision = evaluate_risk(
            capacity_scale=D("1"),
            has_open_position=False,
            emergency_flatten=False,
            scheduled_flatten=False,
            data_fresh=True,
            bid_ask_valid=True,
            market_fields_valid=True,
            broker_connected=True,
            reconciled=True,
            roll_allowed=True,
            margin_reserve_ok=True,
            residual_fraction=D("0.05"),
            max_residual_fraction=D("0.05"),
            portfolio_gross_dv01_usd_per_bp=gross,
            max_portfolio_gross_dv01_usd_per_bp=D("5000"),
            portfolio_net_dv01_usd_per_bp=net,
            max_portfolio_net_dv01_usd_per_bp=D("250"),
            orders_submitted=0,
            max_orders=5,
            working_orders=0,
            max_working_orders=5,
            session_pnl_usd=D("0"),
            max_session_loss_usd=D("1000"),
            drawdown_usd=D("0"),
            max_drawdown_usd=D("1500"),
        )
        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
