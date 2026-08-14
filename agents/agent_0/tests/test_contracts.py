from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from agents.agent_0.contracts import pick_eris_contracts


def detail(contract_id: str, contract_month: str) -> SimpleNamespace:
    return SimpleNamespace(
        contract=SimpleNamespace(conId=contract_id),
        contractMonth=contract_month,
    )


class PickErisContractsTests(unittest.TestCase):
    def test_selects_effective_vintages_newest_first(self) -> None:
        selected = pick_eris_contracts(
            [
                detail("older", "20260813"),
                detail("future", "20260815"),
                detail("at-boundary", "20260814"),
            ],
            count=3,
            as_of=date(2026, 8, 14),
        )

        self.assertEqual(
            [contract.conId for contract in selected],
            ["at-boundary", "older"],
        )

    def test_skips_invalid_contract_months(self) -> None:
        selected = pick_eris_contracts(
            [
                detail("bad-month", "202613"),
                detail("bad-format", "2026-08"),
                detail("missing", ""),
                detail("valid", "202608"),
            ],
            count=4,
            as_of=date(2026, 8, 14),
        )

        self.assertEqual([contract.conId for contract in selected], ["valid"])

    def test_count_caps_selected_contracts(self) -> None:
        selected = pick_eris_contracts(
            [
                detail("oldest", "202606"),
                detail("middle", "202607"),
                detail("newest", "202608"),
            ],
            count=2,
            as_of=date(2026, 8, 14),
        )

        self.assertEqual([contract.conId for contract in selected], ["newest", "middle"])

    def test_nonpositive_count_selects_no_contracts(self) -> None:
        details = [
            detail("older", "202607"),
            detail("valid", "202608"),
        ]

        self.assertEqual(
            pick_eris_contracts(details, count=0, as_of=date(2026, 8, 14)),
            [],
        )
        self.assertEqual(
            pick_eris_contracts(details, count=-1, as_of=date(2026, 8, 14)),
            [],
        )


if __name__ == "__main__":
    unittest.main()
