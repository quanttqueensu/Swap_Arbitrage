from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

from agents.agent_1.contract_risk import (
    ContractRiskError,
    calculate_portfolio_dv01,
    load_contract_risks,
)
from agents.agent_1.models import BoundContract


class ContractRiskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "contract_risk.csv"
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "observation_date", "instrument_id", "dv01_usd_per_bp",
                    "rate_sensitivity_sign", "dv01_method",
                ],
            )
            writer.writeheader()
            writer.writerows([
                {
                    "observation_date": "2026-08-28", "instrument_id": "ERIS-YIT-202606",
                    "dv01_usd_per_bp": "18", "rate_sensitivity_sign": "-1",
                    "dv01_method": "eris_settlement_dv01",
                },
                {
                    "observation_date": "2026-08-29", "instrument_id": "ERIS-YIT-202606",
                    "dv01_usd_per_bp": "19", "rate_sensitivity_sign": "-1",
                    "dv01_method": "eris_settlement_dv01",
                },
                {
                    "observation_date": "2026-08-28", "instrument_id": "YAHOO-CONTINUOUS-ZT",
                    "dv01_usd_per_bp": "38", "rate_sensitivity_sign": "-1",
                    "dv01_method": "cme_fixed_ics_ratio_proxy",
                },
            ])
        self.bindings = {
            "2Y:swap": BoundContract(
                maturity="2Y", leg="swap", con_id=1, symbol="YIT",
                local_symbol="YITM26", min_tick=Decimal("0.005"),
                risk_id="ERIS-YIT-202606",
            ),
            "2Y:treasury": BoundContract(
                maturity="2Y", leg="treasury", con_id=2, symbol="ZT",
                local_symbol="ZTU26", min_tick=Decimal("0.01"),
                risk_id="YAHOO-CONTINUOUS-ZT",
            ),
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_loads_latest_contract_risk_not_after_target_date(self) -> None:
        risks = load_contract_risks(
            self.path,
            as_of=date(2026, 8, 28),
            bindings=self.bindings,
        )
        self.assertEqual(risks[1].dv01_usd_per_bp, Decimal("18"))
        self.assertEqual(risks[2].dv01_usd_per_bp, Decimal("38"))

    def test_calculates_gross_net_and_residual_fraction(self) -> None:
        risks = load_contract_risks(
            self.path,
            as_of=date(2026, 8, 28),
            bindings=self.bindings,
        )
        exposure = calculate_portfolio_dv01({1: 2, 2: -1}, risks)

        self.assertEqual(exposure.gross, Decimal("74"))
        self.assertEqual(exposure.net, Decimal("2"))
        with localcontext() as context:
            context.prec = 50
            expected = Decimal("2") / Decimal("74")
        self.assertEqual(exposure.residual_fraction, expected)

    def test_missing_bound_instrument_risk_fails_closed(self) -> None:
        missing = dict(self.bindings)
        missing["5Y:swap"] = BoundContract(
            maturity="5Y", leg="swap", con_id=3, symbol="YIW",
            local_symbol="YIWM26", min_tick=Decimal("0.005"),
            risk_id="ERIS-YIW-DOES-NOT-EXIST",
        )
        with self.assertRaisesRegex(ContractRiskError, "missing"):
            load_contract_risks(self.path, as_of=date(2026, 8, 28), bindings=missing)


if __name__ == "__main__":
    unittest.main()
