from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from strategy.eris_pricing import ErisParRateQuote
from strategy.live_signal import (
    HistoricalModelState,
    TreasuryYieldQuote,
    evaluate_live_signal,
)


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)


def make_eris(mid_bps: Decimal, observed_at: datetime = NOW) -> ErisParRateQuote:
    return ErisParRateQuote(
        contract_id="YIT-1",
        symbol="YIT",
        observed_at=observed_at,
        bid_price=Decimal("99.99"),
        ask_price=Decimal("100.01"),
        mid_price=Decimal("100.00"),
        bid_par_rate_bps=mid_bps + Decimal("1"),
        ask_par_rate_bps=mid_bps - Decimal("1"),
        mid_par_rate_bps=mid_bps,
    )


def make_treasury(mid_percent: Decimal, observed_at: datetime = NOW) -> TreasuryYieldQuote:
    return TreasuryYieldQuote(
        contract_id="2YY-1",
        symbol="2YY",
        observed_at=observed_at,
        bid_percent=mid_percent - Decimal("0.01"),
        ask_percent=mid_percent + Decimal("0.01"),
        mid_percent=mid_percent,
    )


class LiveSignalTests(unittest.TestCase):
    def test_mid_spread_and_zscore_use_basis_points(self) -> None:
        model = HistoricalModelState(
            version="live_yield_futures_v1",
            mean_bps=Decimal("10"),
            std_bps=Decimal("5"),
            observation_count=252,
        )
        result = evaluate_live_signal(
            maturity="2Y",
            eris=make_eris(Decimal("400")),
            treasury=make_treasury(Decimal("3.80")),
            model=model,
            prior_state=0,
            now=NOW,
            max_quote_age_seconds=30,
        )
        self.assertEqual(result.mid_spread_bps, Decimal("20.00"))
        self.assertEqual(result.z_score, Decimal("2.00"))
        self.assertEqual(result.state, -1)
        self.assertFalse(result.blocked)

    def test_executable_side_bounds_respect_rate_orientation(self) -> None:
        model = HistoricalModelState(
            "live_yield_futures_v1", Decimal("0"), Decimal("10"), 252
        )
        result = evaluate_live_signal(
            maturity="2Y",
            eris=make_eris(Decimal("400")),
            treasury=make_treasury(Decimal("3.80")),
            model=model,
            prior_state=0,
            now=NOW,
            max_quote_age_seconds=30,
        )
        # ERIS ask price maps to the lower par rate; Treasury ask is the higher yield.
        self.assertEqual(result.spread_bid_side_bps, Decimal("18.00"))
        # ERIS bid price maps to the higher par rate; Treasury bid is the lower yield.
        self.assertEqual(result.spread_ask_side_bps, Decimal("22.00"))

    def test_existing_exit_and_reversal_semantics(self) -> None:
        model = HistoricalModelState(
            "live_yield_futures_v1", Decimal("0"), Decimal("10"), 252
        )
        self.assertEqual(self._result_for_spread(Decimal("-5"), model, 1).state, 0)
        self.assertEqual(self._result_for_spread(Decimal("25"), model, 1).state, -1)
        self.assertEqual(self._result_for_spread(Decimal("5"), model, -1).state, 0)
        self.assertEqual(self._result_for_spread(Decimal("-25"), model, -1).state, 1)

    def test_insufficient_history_blocks_and_zeroes_state(self) -> None:
        model = HistoricalModelState(
            "live_yield_futures_v1", Decimal("0"), Decimal("10"), 62
        )
        result = self._result_for_spread(Decimal("20"), model, 1)
        self.assertTrue(result.blocked)
        self.assertEqual(result.state, 0)
        self.assertIn("insufficient_history", result.reason_codes)

    def test_stale_quote_blocks(self) -> None:
        model = HistoricalModelState(
            "live_yield_futures_v1", Decimal("0"), Decimal("10"), 252
        )
        stale = NOW - timedelta(seconds=31)
        result = self._result_for_spread(
            Decimal("20"), model, 0, observed_at=stale
        )
        self.assertTrue(result.blocked)
        self.assertIn("stale_quote", result.reason_codes)

    def test_invalid_quote_timestamp_blocks_before_snapshot_hashing(self) -> None:
        model = HistoricalModelState(
            "live_yield_futures_v1", Decimal("0"), Decimal("10"), 252
        )
        result = evaluate_live_signal(
            maturity="2Y",
            eris=make_eris(Decimal("400"), None),  # type: ignore[arg-type]
            treasury=make_treasury(Decimal("3.80")),
            model=model,
            prior_state=0,
            now=NOW,
            max_quote_age_seconds=30,
        )
        self.assertTrue(result.blocked)
        self.assertIn("invalid_quote_timestamp", result.reason_codes)

    def test_wrong_model_version_blocks(self) -> None:
        model = HistoricalModelState("legacy_dgs", Decimal("0"), Decimal("10"), 252)
        result = self._result_for_spread(Decimal("20"), model, 0)
        self.assertTrue(result.blocked)
        self.assertIn("model_version_mismatch", result.reason_codes)

    def test_zero_historical_standard_deviation_blocks(self) -> None:
        model = HistoricalModelState(
            "live_yield_futures_v1", Decimal("0"), Decimal("0"), 252
        )
        result = self._result_for_spread(Decimal("20"), model, 0)
        self.assertTrue(result.blocked)
        self.assertIn("invalid_historical_std", result.reason_codes)

    def test_identical_snapshot_is_idempotent(self) -> None:
        model = HistoricalModelState(
            "live_yield_futures_v1", Decimal("0"), Decimal("10"), 252
        )
        first = self._result_for_spread(Decimal("20"), model, 0)
        second = self._result_for_spread(Decimal("20"), model, 0)
        self.assertEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(first.z_score, second.z_score)
        self.assertEqual(first.state, second.state)

    def _result_for_spread(
        self,
        spread_bps: Decimal,
        model: HistoricalModelState,
        prior: int,
        observed_at: datetime = NOW,
    ):
        treasury_mid_percent = Decimal("3.80")
        eris_mid_bps = treasury_mid_percent * Decimal("100") + spread_bps
        return evaluate_live_signal(
            maturity="2Y",
            eris=make_eris(eris_mid_bps, observed_at),
            treasury=make_treasury(treasury_mid_percent, observed_at),
            model=model,
            prior_state=prior,
            now=NOW,
            max_quote_age_seconds=30,
        )


if __name__ == "__main__":
    unittest.main()
