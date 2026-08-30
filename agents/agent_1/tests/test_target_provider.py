from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import unittest

from agents.agent_1.targets import load_daily_target
from agents.agent_1.targets import (
    DailyCsvTargetProvider,
    LiveSignalTargetProvider,
    ShadowLiveTargetProvider,
)
from agents.agent_1.targets import TargetValidationError


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)


class FakeShadowRunner:
    def __init__(self) -> None:
        self.calls = []

    def run_once(self, now: datetime, **kwargs) -> str:
        self.calls.append(now)
        return "shadow-result"


class TargetProviderTests(unittest.TestCase):
    def test_daily_provider_preserves_existing_loader_result(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "risk.csv"
            fields = (
                "date",
                "risk_allowed",
                "risk_block_reason",
                "swap_futures_contracts_rounded_2y",
                "treasury_futures_contracts_rounded_2y",
                "swap_futures_contracts_rounded_5y",
                "treasury_futures_contracts_rounded_5y",
            )
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow({
                    "date": "2026-08-28",
                    "risk_allowed": "1",
                    "risk_block_reason": "",
                    "swap_futures_contracts_rounded_2y": "2",
                    "treasury_futures_contracts_rounded_2y": "-1",
                    "swap_futures_contracts_rounded_5y": "-3",
                    "treasury_futures_contracts_rounded_5y": "3",
                })

            expected = load_daily_target(
                path, now=NOW, max_age_business_days=2
            )
            actual = DailyCsvTargetProvider(path, 2).load_target(NOW)
            self.assertEqual(actual, expected)

    def test_shadow_provider_observes_without_exposing_load_target(self) -> None:
        runner = FakeShadowRunner()
        provider = ShadowLiveTargetProvider(runner)
        self.assertEqual(provider.observe(NOW), "shadow-result")
        self.assertEqual(runner.calls, [NOW])
        self.assertFalse(hasattr(provider, "load_target"))

    def test_live_provider_refreshes_and_converts_hypothetical_target(self) -> None:
        maturity_targets = {
            "2Y": SimpleNamespace(swap_quantity=-12, treasury_quantity=6, blocked=False, reason_codes=()),
            "5Y": SimpleNamespace(swap_quantity=8, treasury_quantity=-8, blocked=False, reason_codes=()),
        }
        cycle = SimpleNamespace(
            hypothetical_target=SimpleNamespace(
                strategy_version="live_yield_futures_v1",
                maturities=maturity_targets,
                blocked=False,
                reason_codes=("within_limits",),
            )
        )
        runner = SimpleNamespace(run_once=lambda now, **kwargs: cycle)
        refresher = SimpleNamespace(
            refresh=lambda now: SimpleNamespace(risk_inputs={"2Y": object(), "5Y": object()})
        )
        provider = LiveSignalTargetProvider(runner, refresher)

        target = provider.load_target(NOW)

        self.assertEqual(target.target_2y.swap_qty, -12)
        self.assertEqual(target.target_2y.treasury_qty, 6)
        self.assertEqual(target.target_5y.swap_qty, 8)
        self.assertEqual(target.target_5y.treasury_qty, -8)
        self.assertEqual(target.age_business_days, 0)

    def test_live_provider_rejects_blocked_target(self) -> None:
        maturity_targets = {
            maturity: SimpleNamespace(
                swap_quantity=0,
                treasury_quantity=0,
                blocked=True,
                reason_codes=("missing_historical_model_state",),
            )
            for maturity in ("2Y", "5Y")
        }
        cycle = SimpleNamespace(
            hypothetical_target=SimpleNamespace(
                strategy_version="live_yield_futures_v1",
                maturities=maturity_targets,
                blocked=True,
                reason_codes=("2Y:blocked", "5Y:blocked"),
            )
        )
        provider = LiveSignalTargetProvider(
            SimpleNamespace(run_once=lambda now, **kwargs: cycle),
            SimpleNamespace(refresh=lambda now: SimpleNamespace(risk_inputs={})),
        )
        with self.assertRaisesRegex(TargetValidationError, "Live signal target is blocked"):
            provider.load_target(NOW)


if __name__ == "__main__":
    unittest.main()
