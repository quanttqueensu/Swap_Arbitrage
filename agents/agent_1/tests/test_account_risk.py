from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace

from agents.agent_1.account_risk import AccountRiskError, collect_session_pnl, update_drawdown


class FakeIB:
    def __init__(self, daily_pnl):
        self.daily_pnl = daily_pnl
        self.cancelled = []
    def reqPnL(self, account, model_code):
        self.request = (account, model_code)
        return SimpleNamespace(dailyPnL=self.daily_pnl)
    def sleep(self, seconds):
        self.slept = seconds
    def cancelPnL(self, account, model_code):
        self.cancelled.append((account, model_code))


class AccountRiskTests(unittest.TestCase):
    def test_collects_finite_ibkr_daily_pnl_and_cancels_subscription(self):
        ib = FakeIB("125.50")
        pnl = collect_session_pnl(ib, "DU123", wait_seconds=0.1)
        self.assertEqual(pnl, Decimal("125.50"))
        self.assertEqual(ib.request, ("DU123", ""))
        self.assertEqual(ib.cancelled, [("DU123", "")])

    def test_missing_or_nonfinite_daily_pnl_fails_closed(self):
        for value in (None, float("nan"), "Infinity"):
            with self.subTest(value=value):
                with self.assertRaises(AccountRiskError):
                    collect_session_pnl(FakeIB(value), "DU123")

    def test_drawdown_tracks_session_high_water_mark(self):
        first = update_drawdown(Decimal("0"), Decimal("100"))
        self.assertEqual((first.peak_pnl_usd, first.drawdown_usd), (Decimal("100"), Decimal("0")))
        second = update_drawdown(first.peak_pnl_usd, Decimal("60"))
        self.assertEqual((second.peak_pnl_usd, second.drawdown_usd), (Decimal("100"), Decimal("40")))
        third = update_drawdown(second.peak_pnl_usd, Decimal("120"))
        self.assertEqual((third.peak_pnl_usd, third.drawdown_usd), (Decimal("120"), Decimal("0")))


if __name__ == "__main__":
    unittest.main()
