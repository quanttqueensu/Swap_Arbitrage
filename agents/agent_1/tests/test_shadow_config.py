from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest

from agents.agent_1.shadow import load_shadow_settings


class ShadowConfigTests(unittest.TestCase):
    def test_example_loads_with_agent_risk_caps(self) -> None:
        config = SimpleNamespace(
            max_2y_swap_contracts=20,
            max_2y_treasury_contracts=10,
            max_5y_swap_contracts=12,
            max_5y_treasury_contracts=12,
        )
        path = Path(__file__).resolve().parents[1] / "agent1.shadow.example.json"
        settings = load_shadow_settings(path, config)
        self.assertEqual(settings.risk_inputs["2Y"].base_target_dv01, Decimal("3000"))
        self.assertEqual(settings.risk_inputs["2Y"].max_swap_contracts, 20)
        self.assertEqual(settings.risk_inputs["5Y"].max_treasury_contracts, 12)
        self.assertEqual(settings.quote_max_age_seconds, 30)


if __name__ == "__main__":
    unittest.main()
