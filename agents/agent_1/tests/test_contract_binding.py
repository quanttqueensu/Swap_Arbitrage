from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from agents.agent_1.contracts import ContractSelectionError, resolve_binding


def detail(con_id, symbol, local_symbol, last_trade, *, contract_month="", min_tick="0.005"):
    contract = SimpleNamespace(
        conId=con_id,
        symbol=symbol,
        localSymbol=local_symbol,
        lastTradeDateOrContractMonth=last_trade,
        contractMonth=contract_month,
    )
    return SimpleNamespace(
        contract=contract,
        contractMonth=contract_month,
        minTick=Decimal(min_tick),
    )


class FakeIB:
    def __init__(self, details):
        self.details = details
        self.probes = []
        self.qualified = []

    def reqContractDetails(self, probe):
        self.probes.append(probe)
        return list(self.details)

    def qualifyContracts(self, contract):
        self.qualified.append(contract)
        return [contract]


class ContractBindingTests(unittest.TestCase):
    def test_resolves_eris_binding_with_vintage_risk_id_and_min_tick(self) -> None:
        ib = FakeIB([
            detail(10, "YIT", "YITH26", "20280320", contract_month="202603"),
            detail(11, "YIT", "YITM26", "20280620", contract_month="202606"),
            detail(12, "YIT", "YITU26", "20280918", contract_month="202609"),
        ])
        binding = resolve_binding(
            ib,
            maturity="2Y",
            leg="swap",
            symbol="YIT",
            kind="swap_future",
            as_of=date(2026, 8, 29),
            min_days_to_expiry=14,
            exchanges=("CBOT",),
            future_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        )

        self.assertEqual(binding.con_id, 11)
        self.assertEqual(binding.risk_id, "ERIS-YIT-202606")
        self.assertEqual(binding.min_tick, Decimal("0.005"))
        self.assertEqual(binding.local_symbol, "YITM26")
        self.assertIs(binding.broker_contract, ib.qualified[0])

    def test_resolves_treasury_binding_to_continuous_proxy_risk_id(self) -> None:
        ib = FakeIB([
            detail(20, "ZT", "ZTU26", "20260918", min_tick="0.0078125"),
            detail(21, "ZT", "ZTZ26", "20261218", min_tick="0.0078125"),
        ])
        binding = resolve_binding(
            ib,
            maturity="2Y",
            leg="treasury",
            symbol="ZT",
            kind="treasury_future",
            as_of=date(2026, 8, 29),
            min_days_to_expiry=14,
            exchanges=("CBOT",),
            future_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        )

        self.assertEqual(binding.con_id, 20)
        self.assertEqual(binding.risk_id, "YAHOO-CONTINUOUS-ZT")

    def test_invalid_min_tick_fails_closed(self) -> None:
        ib = FakeIB([detail(20, "ZT", "ZTU26", "20260918", min_tick="0")])
        with self.assertRaisesRegex(ContractSelectionError, "tick"):
            resolve_binding(
                ib,
                maturity="2Y", leg="treasury", symbol="ZT", kind="treasury_future",
                as_of=date(2026, 8, 29), min_days_to_expiry=14,
                exchanges=("CBOT",), future_factory=lambda **kwargs: SimpleNamespace(**kwargs),
            )


if __name__ == "__main__":
    unittest.main()
