from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from agents.agent_1.models import BoundContract, BrokerSnapshot, QuoteSnapshot
from agents.agent_1.cycle import RuntimeCache, status_cycle
from agents.agent_1.state import AgentState


class FakeIB:
    def __init__(self, daily_pnl="0"):
        self.place_calls = []
        self.what_if_calls = []
        self.daily_pnl = daily_pnl

    def reqPnL(self, account, model_code):
        return SimpleNamespace(dailyPnL=self.daily_pnl)

    def cancelPnL(self, account, model_code):
        return None

    def sleep(self, seconds):
        return None

    def whatIfOrder(self, contract, order):
        self.what_if_calls.append((contract, order))
        return SimpleNamespace(equityWithLoanAfter="100000", initMarginAfter="50000")

    def placeOrder(self, contract, order):
        self.place_calls.append((contract, order))
        raise AssertionError("status must never transmit")


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.target_path = root / "risk_data.csv"
        with self.target_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "date", "risk_allowed", "risk_block_reason",
                "swap_futures_contracts_rounded_2y", "treasury_futures_contracts_rounded_2y",
                "swap_futures_contracts_rounded_5y", "treasury_futures_contracts_rounded_5y",
            ])
            writer.writeheader()
            writer.writerow({
                "date": "2026-08-31", "risk_allowed": "1", "risk_block_reason": "",
                "swap_futures_contracts_rounded_2y": "2", "treasury_futures_contracts_rounded_2y": "-1",
                "swap_futures_contracts_rounded_5y": "0", "treasury_futures_contracts_rounded_5y": "0",
            })
        self.contract_risk_path = root / "contract_risk.csv"
        with self.contract_risk_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "observation_date", "instrument_id", "dv01_usd_per_bp",
                "rate_sensitivity_sign", "dv01_method",
            ])
            writer.writeheader()
            for risk_id, dv01 in (("r1", "20"), ("r2", "40"), ("r3", "40"), ("r4", "40")):
                writer.writerow({
                    "observation_date": "2026-08-31", "instrument_id": risk_id,
                    "dv01_usd_per_bp": dv01, "rate_sensitivity_sign": "-1",
                    "dv01_method": "test",
                })
        self.now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
        self.config = SimpleNamespace(
            account="DU123", client_id=31,
            max_target_age_business_days=2,
            max_quote_age_seconds=Decimal("10"),
            order_group_timeout_seconds=Decimal("30"),
            max_residual_dv01_fraction=Decimal("0.10"),
            max_gross_dv01=Decimal("10000"), max_net_dv01=Decimal("250"),
            max_order_groups_per_session=50, max_working_order_groups=2,
            max_session_loss_usd=Decimal("1000"), max_drawdown_usd=Decimal("1000"),
            max_2y_swap_contracts=100, max_2y_treasury_contracts=100,
            max_5y_swap_contracts=100, max_5y_treasury_contracts=100,
            margin_reserve_fraction=Decimal("0.15"),
        )
        self.bindings = {}
        for key, con_id, symbol, risk_id in (
            ("2Y:swap", 1, "YIT", "r1"),
            ("2Y:treasury", 2, "ZT", "r2"),
            ("5Y:swap", 3, "YIW", "r3"),
            ("5Y:treasury", 4, "ZF", "r4"),
        ):
            maturity, leg = key.split(":")
            self.bindings[key] = BoundContract(
                maturity=maturity, leg=leg, con_id=con_id, symbol=symbol,
                local_symbol=symbol + "X", min_tick=Decimal("0.01"), risk_id=risk_id,
                broker_contract=SimpleNamespace(conId=con_id),
            )
        self.snapshot = BrokerSnapshot(
            observed_at=self.now,
            positions={1: 0, 2: 0, 3: 0, 4: 0},
            working_orders=(),
            quotes={
                con_id: QuoteSnapshot(con_id, Decimal("99"), Decimal("99.01"), self.now)
                for con_id in (1, 2, 3, 4)
            },
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_status_cycle_previews_margin_but_never_places_orders(self) -> None:
        ib = FakeIB()
        risk_calls = []

        def evaluator(**kwargs):
            risk_calls.append(kwargs)
            return SimpleNamespace(
                allowed=True, flatten_requested=False, reason_codes=("within_limits",),
            )

        result = status_cycle(
            ib=ib,
            config=self.config,
            target_path=self.target_path,
            contract_risk_path=self.contract_risk_path,
            state=AgentState(),
            now=self.now,
            binding_resolver=lambda *args, **kwargs: self.bindings,
            snapshot_collector=lambda *args, **kwargs: self.snapshot,
            evaluator=evaluator,
            order_factory=lambda action, qty, price: SimpleNamespace(
                action=action, totalQuantity=qty, lmtPrice=price, orderType="LMT"
            ),
        )

        self.assertEqual(result.plan.action, "trade")
        self.assertTrue(result.margin_reserve_ok)
        self.assertEqual(len(ib.what_if_calls), 2)
        self.assertEqual(ib.place_calls, [])
        self.assertEqual(result.target.target_2y.swap_qty, 2)
        self.assertEqual(len(risk_calls), 1)

    def test_runtime_cache_reuses_same_day_contract_bindings(self) -> None:
        ib = FakeIB()
        cache = RuntimeCache()
        calls = []

        def resolver(*args, **kwargs):
            calls.append(kwargs["as_of"])
            return self.bindings

        kwargs = dict(
            ib=ib, config=self.config, target_path=self.target_path,
            contract_risk_path=self.contract_risk_path,
            state=AgentState(session_pnl_date="2026-08-31"), now=self.now,
            binding_resolver=resolver,
            snapshot_collector=lambda *args, **kwargs: self.snapshot,
            evaluator=lambda **kwargs: SimpleNamespace(
                allowed=True, flatten_requested=False, reason_codes=("within_limits",),
            ),
            order_factory=lambda action, qty, price: SimpleNamespace(
                action=action, totalQuantity=qty, lmtPrice=price, orderType="LMT"
            ),
            runtime_cache=cache,
        )
        status_cycle(**kwargs)
        status_cycle(**kwargs)

        self.assertEqual(calls, [date(2026, 8, 31)])

    def test_active_group_cycle_skips_normal_planning_and_margin_preview(self) -> None:
        ib = FakeIB()
        state = AgentState(
            active_groups={"A1:2Y:target:0001": {"maturity": "2Y"}},
            session_pnl_date="2026-08-31",
        )

        def evaluator(**kwargs):
            raise AssertionError("active-group lifecycle must own this cycle")

        result = status_cycle(
            ib=ib, config=self.config, target_path=self.target_path,
            contract_risk_path=self.contract_risk_path, state=state, now=self.now,
            binding_resolver=lambda *args, **kwargs: self.bindings,
            snapshot_collector=lambda *args, **kwargs: self.snapshot,
            evaluator=evaluator,
            order_factory=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("active-group cycle must not build normal orders")
            ),
        )

        self.assertEqual(result.plan.action, "hold")
        self.assertEqual(result.plan.reason_codes, ("active_group_pending",))
        self.assertEqual(ib.what_if_calls, [])

    def test_status_cycle_marks_reconciliation_mismatch_and_blocks_when_flat(self) -> None:
        ib = FakeIB()
        state = AgentState(bound_contracts={"2Y:swap": 999})
        result = status_cycle(
            ib=ib, config=self.config, target_path=self.target_path,
            contract_risk_path=self.contract_risk_path, state=state, now=self.now,
            binding_resolver=lambda *args, **kwargs: self.bindings,
            snapshot_collector=lambda *args, **kwargs: self.snapshot,
            evaluator=lambda **kwargs: SimpleNamespace(
                allowed=kwargs["reconciled"],
                flatten_requested=False,
                reason_codes=("within_limits",) if kwargs["reconciled"] else ("reconciliation_mismatch",),
            ),
            order_factory=lambda action, qty, price: SimpleNamespace(
                action=action, totalQuantity=qty, lmtPrice=price, orderType="LMT"
            ),
        )
        self.assertFalse(result.recovery.reconciled)
        self.assertEqual(result.plan.action, "blocked")
        self.assertIn("reconciliation_mismatch", result.plan.reason_codes)

    def test_status_cycle_threads_broker_daily_pnl_and_session_drawdown_into_risk(self) -> None:
        ib = FakeIB(daily_pnl="60")
        state = AgentState(session_pnl_date="2026-08-31", session_peak_pnl_usd=Decimal("100"))
        captured = {}
        def evaluator(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(allowed=True, flatten_requested=False, reason_codes=("within_limits",))
        result = status_cycle(
            ib=ib, config=self.config, target_path=self.target_path,
            contract_risk_path=self.contract_risk_path, state=state, now=self.now,
            binding_resolver=lambda *args, **kwargs: self.bindings,
            snapshot_collector=lambda *args, **kwargs: self.snapshot,
            evaluator=evaluator,
            order_factory=lambda action, qty, price: SimpleNamespace(
                action=action, totalQuantity=qty, lmtPrice=price, orderType="LMT"
            ),
        )
        self.assertEqual(captured["session_pnl_usd"], Decimal("60"))
        self.assertEqual(captured["drawdown_usd"], Decimal("40"))
        self.assertEqual(result.session_peak_pnl_usd, Decimal("100"))
        self.assertEqual(result.session_pnl_date, "2026-08-31")


if __name__ == "__main__":
    unittest.main()
