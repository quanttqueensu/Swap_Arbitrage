from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from agents.agent_1.contracts import ContractSelectionError, select_contract


def detail(
    con_id: int,
    symbol: str,
    last_trade: str,
    *,
    contract_month: str = "",
):
    contract = SimpleNamespace(
        conId=con_id,
        symbol=symbol,
        lastTradeDateOrContractMonth=last_trade,
    )
    return SimpleNamespace(contract=contract, contractMonth=contract_month)


class ContractSelectionTests(unittest.TestCase):
    def test_treasury_selects_nearest_eligible_expiry(self) -> None:
        selected = select_contract(
            [
                detail(1, "ZT", "20260910"),
                detail(2, "ZT", "20260918"),
                detail(3, "ZT", "20261218"),
            ],
            kind="treasury_future",
            as_of=date(2026, 8, 29),
            min_days_to_expiry=14,
        )

        self.assertEqual(selected.conId, 2)

    def test_retains_eligible_held_contract(self) -> None:
        selected = select_contract(
            [
                detail(1, "ZF", "20260918"),
                detail(2, "ZF", "20261218"),
            ],
            kind="treasury_future",
            as_of=date(2026, 8, 29),
            min_days_to_expiry=14,
            held_con_id=2,
        )

        self.assertEqual(selected.conId, 2)

    def test_eris_uses_newest_non_forward_vintage_with_eligible_maturity(self) -> None:
        selected = select_contract(
            [
                detail(10, "YIT", "20280320", contract_month="202603"),
                detail(11, "YIT", "20280620", contract_month="202606"),
                detail(12, "YIT", "20280918", contract_month="202609"),
            ],
            kind="swap_future",
            as_of=date(2026, 8, 29),
            min_days_to_expiry=14,
        )

        self.assertEqual(selected.conId, 11)

    def test_rejects_when_no_contract_meets_expiry_policy(self) -> None:
        with self.assertRaisesRegex(ContractSelectionError, "eligible"):
            select_contract(
                [detail(1, "ZT", "20260905")],
                kind="treasury_future",
                as_of=date(2026, 8, 29),
                min_days_to_expiry=14,
            )


if __name__ == "__main__":
    unittest.main()
