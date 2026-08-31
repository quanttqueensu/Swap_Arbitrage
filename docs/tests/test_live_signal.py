from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import unittest

from data_pipeline.live_data_pipeline.live_market_source import ContractRequest, MarketDataError
from data_pipeline.live_data_pipeline.eris_reference_data import ErisReferenceError
from data_pipeline.live_data_pipeline.live_signal_runner import LiveSignalRunner
from strategy.eris_pricing import ErisReference, PriceQuote
from strategy.live_signal import HistoricalModelState, TreasuryYieldQuote
from strategy.live_target import MaturityRiskInputs


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)


class FakeMarketSource:
    def __init__(
        self,
        missing_symbol: str | None = None,
        error_reason: str = "missing_market_quote",
    ):
        self.missing_symbol = missing_symbol
        self.error_reason = error_reason
        self.calls = []

    def snapshot(self, requests: list[ContractRequest], *, now: datetime):
        self.calls.append(tuple(request.symbol for request in requests))
        output = {}
        for request in requests:
            if request.symbol == self.missing_symbol:
                raise MarketDataError(
                    self.error_reason, f"quote error for {request.symbol}"
                )
            if request.symbol == "YIT":
                output[request.symbol] = PriceQuote(
                    "YIT-1", "YIT", now,
                    Decimal("99.98"), Decimal("100.02"), Decimal("100"), Decimal("0.001")
                )
            elif request.symbol == "YIW":
                output[request.symbol] = PriceQuote(
                    "YIW-1", "YIW", now,
                    Decimal("99.98"), Decimal("100.02"), Decimal("100"), Decimal("0.001")
                )
            elif request.symbol == "2YY":
                output[request.symbol] = TreasuryYieldQuote(
                    "2YY-1", "2YY", now,
                    Decimal("3.79"), Decimal("3.81"), Decimal("3.80")
                )
            elif request.symbol == "5YY":
                output[request.symbol] = TreasuryYieldQuote(
                    "5YY-1", "5YY", now,
                    Decimal("3.49"), Decimal("3.51"), Decimal("3.50")
                )
        return output


class FakeReferenceProvider:
    def __init__(self, error_symbol: str | None = None) -> None:
        self.error_symbol = error_symbol

    def get(self, contract_id: str, symbol: str, as_of: datetime):
        if symbol == self.error_symbol:
            raise ErisReferenceError(
                "stale_eris_reference", f"stale reference for {symbol}"
            )
        fixed = Decimal("0.04") if symbol == "YIT" else Decimal("0.037")
        return ErisReference(
            contract_id=contract_id,
            fixed_rate_decimal=fixed,
            b_price_points=Decimal("0"),
            c_price_points=Decimal("0"),
            pv01_usd_per_bp=Decimal("20") if symbol == "YIT" else Decimal("50"),
            effective_date="2026-06-17",
            maturity_date="2028-06-21" if symbol == "YIT" else "2031-06-21",
            observed_at=as_of,
        )


def model_loader(_path: Path, maturity: str, _as_of: datetime):
    # YIT mid converts to 400 bps and 2YY mid is 380 -> spread 20 -> z +2.
    # YIW mid converts to 370 bps and 5YY mid is 350 -> spread 20 -> z +2.
    return HistoricalModelState(
        version="live_yield_futures_v1",
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
    def test_complete_cycle_writes_two_rows_and_exposes_a_live_target(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = LiveSignalRunner(
                market_source=FakeMarketSource(),
                reference_provider=FakeReferenceProvider(),
                model_state_path=root / "baseline.csv",
                model_state_loader=model_loader,
                risk_inputs=risks(),
                audit_path=root / "live_signals.csv",
                state_path=root / "signal_state.json",
            )
            result = runner.run_once(NOW)

            self.assertEqual(set(result.signals), {"2Y", "5Y"})
            self.assertEqual(result.signals["2Y"].state, -1)
            self.assertEqual(result.signals["5Y"].state, -1)
            self.assertNotEqual(
                result.target.maturities["2Y"].swap_quantity, 0
            )
            self.assertLessEqual(
                result.target.gross_target_dv01, Decimal("10000")
            )

            with (root / "live_signals.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["maturity"] for row in rows}, {"2Y", "5Y"})
            self.assertTrue(all(row["strategy_version"] == "live_yield_futures_v1" for row in rows))
            self.assertTrue(all(row["blocked"] == "0" for row in rows))

    def test_duplicate_poll_appends_observations_and_restart_restores_state(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kwargs = dict(
                market_source=FakeMarketSource(),
                reference_provider=FakeReferenceProvider(),
                model_state_path=root / "baseline.csv",
                model_state_loader=model_loader,
                risk_inputs=risks(),
                audit_path=root / "live_signals.csv",
                state_path=root / "signal_state.json",
            )
            first_runner = LiveSignalRunner(**kwargs)
            first = first_runner.run_once(NOW)
            second = first_runner.run_once(NOW)
            restarted = LiveSignalRunner(**kwargs).run_once(NOW)

            self.assertEqual(first.signals["2Y"].state, -1)
            self.assertEqual(second.signals["2Y"].prior_state, -1)
            self.assertEqual(restarted.signals["2Y"].prior_state, -1)
            with (root / "live_signals.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 6)

    def test_missing_5y_quote_blocks_only_5y_hypothetical_exposure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = LiveSignalRunner(
                market_source=FakeMarketSource(missing_symbol="5YY"),
                reference_provider=FakeReferenceProvider(),
                model_state_path=root / "baseline.csv",
                model_state_loader=model_loader,
                risk_inputs=risks(),
                audit_path=root / "live_signals.csv",
                state_path=root / "signal_state.json",
            )
            result = runner.run_once(NOW)
            self.assertFalse(result.signals["2Y"].blocked)
            self.assertTrue(result.signals["5Y"].blocked)
            self.assertIn("missing_market_quote", result.signals["5Y"].reason_codes)
            self.assertEqual(result.target.maturities["5Y"].swap_quantity, 0)
            self.assertNotEqual(result.target.maturities["2Y"].swap_quantity, 0)

    def test_market_and_reference_failures_keep_specific_reason_codes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = LiveSignalRunner(
                market_source=FakeMarketSource(
                    missing_symbol="2YY", error_reason="stale_quote"
                ),
                reference_provider=FakeReferenceProvider(error_symbol="YIW"),
                model_state_path=root / "baseline.csv",
                model_state_loader=model_loader,
                risk_inputs=risks(),
                audit_path=root / "live_signals.csv",
                state_path=root / "signal_state.json",
            )
            result = runner.run_once(NOW)
            self.assertIn("stale_quote", result.signals["2Y"].reason_codes)
            self.assertIn(
                "stale_eris_reference", result.signals["5Y"].reason_codes
            )

if __name__ == "__main__":
    unittest.main()
