from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import unittest

from data_pipeline.live_data_pipeline.live_signal_runner import LiveSignalRunner
from strategy.live_signal import (
    DailySpreadObservation,
    HistoricalModelState,
    LIVE_SIGNAL_STRATEGY_VERSION,
)
from strategy.live_target import MaturityRiskInputs


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)
OBSERVED = datetime(2026, 8, 28, 21, 0, tzinfo=timezone.utc)


def observation_loader(_path: Path, maturity: str, _as_of: datetime):
    if maturity == "2Y":
        return DailySpreadObservation(
            "2Y", OBSERVED, Decimal("400"), "DGS2", Decimal("380"), Decimal("20")
        )
    return DailySpreadObservation(
        "5Y", OBSERVED, Decimal("370"), "DGS5", Decimal("350"), Decimal("20")
    )


def model_loader(_path: Path, _maturity: str, _as_of: datetime):
    return HistoricalModelState(
        version=LIVE_SIGNAL_STRATEGY_VERSION,
        mean_bps=Decimal("0"),
        std_bps=Decimal("10"),
        observation_count=252,
    )


def risks():
    return {
        "2Y": MaturityRiskInputs(
            Decimal("3000"), Decimal("1"), Decimal("20"), Decimal("40")
        ),
        "5Y": MaturityRiskInputs(
            Decimal("3000"), Decimal("1"), Decimal("50"), Decimal("50")
        ),
    }


class LiveSignalTests(unittest.TestCase):
    def runner(self, root: Path, **overrides) -> LiveSignalRunner:
        kwargs = {
            "model_state_path": root / "baseline.csv",
            "model_state_loader": model_loader,
            "observation_loader": observation_loader,
            "risk_inputs": risks(),
            "audit_path": root / "live_signals.csv",
            "state_path": root / "signal_state.json",
        }
        kwargs.update(overrides)
        return LiveSignalRunner(**kwargs)

    def test_daily_cycle_uses_aligned_fred_rows_and_writes_target(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.runner(root).run_once(NOW)

            self.assertEqual(result.observation_time_utc, OBSERVED)
            self.assertEqual(result.signals["2Y"].state, -1)
            self.assertEqual(result.signals["5Y"].state, -1)
            self.assertNotEqual(result.target.maturities["2Y"].swap_quantity, 0)
            with (root / "live_signals.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual({row["fred_series"] for row in rows}, {"DGS2", "DGS5"})
            self.assertTrue(all(row["strategy_version"] == LIVE_SIGNAL_STRATEGY_VERSION for row in rows))

    def test_duplicate_poll_restores_state_without_changing_daily_input(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.runner(root).run_once(NOW)
            second = self.runner(root).run_once(NOW + timedelta(minutes=1))
            self.assertEqual(first.signals["2Y"].state, -1)
            self.assertEqual(second.signals["2Y"].prior_state, -1)
            self.assertEqual(second.signals["2Y"].mid_spread_bps, Decimal("20"))

    def test_missing_5y_daily_row_blocks_only_5y(self) -> None:
        def missing(_path: Path, maturity: str, as_of: datetime):
            if maturity == "5Y":
                raise RuntimeError("missing")
            return observation_loader(_path, maturity, as_of)

        with TemporaryDirectory() as tmp:
            result = self.runner(Path(tmp), observation_loader=missing).run_once(NOW)
            self.assertFalse(result.signals["2Y"].blocked)
            self.assertTrue(result.signals["5Y"].blocked)
            self.assertIn("missing_daily_observation", result.signals["5Y"].reason_codes)

    def test_mismatched_daily_dates_block_both_maturities(self) -> None:
        def mismatched(path: Path, maturity: str, as_of: datetime):
            row = observation_loader(path, maturity, as_of)
            if maturity == "5Y":
                return DailySpreadObservation(
                    row.maturity,
                    row.observed_at - timedelta(days=1),
                    row.eris_rate_bps,
                    row.fred_series,
                    row.treasury_rate_bps,
                    row.spread_bps,
                )
            return row

        with TemporaryDirectory() as tmp:
            result = self.runner(Path(tmp), observation_loader=mismatched).run_once(NOW)
            self.assertTrue(all(signal.blocked for signal in result.signals.values()))
            self.assertTrue(
                all("misaligned_observation_dates" in signal.reason_codes for signal in result.signals.values())
            )


if __name__ == "__main__":
    unittest.main()
