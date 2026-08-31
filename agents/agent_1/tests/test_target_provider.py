from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import unittest
from unittest.mock import Mock, patch

from agents.agent_1.targets import load_daily_target
from agents.agent_1.targets import (
    DailyCsvTargetProvider,
    LiveSignalTargetProvider,
)
from agents.agent_1.targets import TargetValidationError
from strategy.live_signal import LIVE_SIGNAL_STRATEGY_VERSION


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)


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

    def test_live_provider_refreshes_and_converts_target(self) -> None:
        maturity_targets = {
            "2Y": SimpleNamespace(swap_quantity=-12, treasury_quantity=6, blocked=False, reason_codes=()),
            "5Y": SimpleNamespace(swap_quantity=8, treasury_quantity=-8, blocked=False, reason_codes=()),
        }
        cycle = SimpleNamespace(
            observation_time_utc=datetime(2026, 8, 28, 21, tzinfo=timezone.utc),
            target=SimpleNamespace(
                strategy_version=LIVE_SIGNAL_STRATEGY_VERSION,
                maturities=maturity_targets,
                blocked=False,
                reason_codes=("within_limits",),
            )
        )
        runner = SimpleNamespace(run_once=Mock(return_value=cycle))
        risk_inputs = {"2Y": object(), "5Y": object()}
        refresher = SimpleNamespace(
            agent_config=SimpleNamespace(max_target_age_business_days=2),
            refresh=lambda now: SimpleNamespace(risk_inputs=risk_inputs)
        )
        provider = LiveSignalTargetProvider(runner, refresher)

        observed = datetime(2026, 8, 30, 14, 1, tzinfo=timezone.utc)
        with patch("agents.agent_1.targets.datetime") as clock:
            clock.now.return_value = observed
            target = provider.load_target(NOW)

        runner.run_once.assert_called_once_with(observed, risk_inputs=risk_inputs)
        self.assertEqual(target.target_2y.swap_qty, -12)
        self.assertEqual(target.target_2y.treasury_qty, 6)
        self.assertEqual(target.target_5y.swap_qty, 8)
        self.assertEqual(target.target_5y.treasury_qty, -8)
        self.assertEqual(target.age_business_days, 0)
        self.assertEqual(target.as_of.isoformat(), "2026-08-28")

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
            observation_time_utc=datetime(2026, 8, 28, 21, tzinfo=timezone.utc),
            target=SimpleNamespace(
                strategy_version=LIVE_SIGNAL_STRATEGY_VERSION,
                maturities=maturity_targets,
                blocked=True,
                reason_codes=("2Y:blocked", "5Y:blocked"),
            )
        )
        provider = LiveSignalTargetProvider(
            SimpleNamespace(run_once=lambda now, **kwargs: cycle),
            SimpleNamespace(
                agent_config=SimpleNamespace(max_target_age_business_days=2),
                refresh=lambda now: SimpleNamespace(risk_inputs={}),
            ),
        )
        with self.assertRaisesRegex(TargetValidationError, "Live signal target is blocked"):
            provider.load_target(NOW)


if __name__ == "__main__":
    unittest.main()
