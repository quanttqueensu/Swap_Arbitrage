from datetime import datetime, timezone
from decimal import Decimal
import unittest

from strategy.eris_pricing import (
    ErisReference,
    PriceQuote,
    convert_eris_quote,
    eris_a_usd,
    eris_par_rate_bps,
)


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)


class ErisPricingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = ErisReference(
            contract_id="YIT-TEST",
            fixed_rate_decimal=Decimal("0.0400"),
            b_price_points=Decimal("0.125"),
            c_price_points=Decimal("0.025"),
            pv01_usd_per_bp=Decimal("20"),
            effective_date="2026-06-17",
            maturity_date="2028-06-21",
            observed_at=NOW,
        )

    def test_zero_a_returns_fixed_coupon(self) -> None:
        price = Decimal("100.100")
        self.assertEqual(eris_a_usd(price, self.reference), Decimal("0.000"))
        self.assertEqual(
            eris_par_rate_bps(price, self.reference), Decimal("400.0000")
        )

    def test_positive_a_reduces_equivalent_par_rate(self) -> None:
        self.assertEqual(
            eris_a_usd(Decimal("100.120"), self.reference), Decimal("20.000")
        )
        self.assertEqual(
            eris_par_rate_bps(Decimal("100.120"), self.reference),
            Decimal("399.0000"),
        )

    def test_negative_a_increases_equivalent_par_rate(self) -> None:
        self.assertEqual(
            eris_a_usd(Decimal("100.080"), self.reference), Decimal("-20.000")
        )
        self.assertEqual(
            eris_par_rate_bps(Decimal("100.080"), self.reference),
            Decimal("401.0000"),
        )

    def test_bid_ask_mid_are_converted(self) -> None:
        quote = PriceQuote(
            contract_id="YIT-TEST",
            symbol="YIT",
            observed_at=NOW,
            bid=Decimal("100.080"),
            ask=Decimal("100.120"),
            last=None,
            min_tick=Decimal("0.001"),
        )
        converted = convert_eris_quote(quote, self.reference)
        self.assertEqual(converted.mid_price, Decimal("100.100"))
        self.assertEqual(converted.bid_par_rate_bps, Decimal("401.0000"))
        self.assertEqual(converted.ask_par_rate_bps, Decimal("399.0000"))
        self.assertEqual(converted.mid_par_rate_bps, Decimal("400.0000"))

    def test_contract_mismatch_is_rejected(self) -> None:
        quote = PriceQuote(
            contract_id="OTHER",
            symbol="YIT",
            observed_at=NOW,
            bid=Decimal("100.080"),
            ask=Decimal("100.120"),
            last=None,
            min_tick=Decimal("0.001"),
        )
        with self.assertRaisesRegex(ValueError, "contract mismatch"):
            convert_eris_quote(quote, self.reference)

    def test_crossed_quote_is_rejected(self) -> None:
        quote = PriceQuote(
            contract_id="YIT-TEST",
            symbol="YIT",
            observed_at=NOW,
            bid=Decimal("100.120"),
            ask=Decimal("100.080"),
            last=None,
            min_tick=Decimal("0.001"),
        )
        with self.assertRaisesRegex(ValueError, "crossed"):
            convert_eris_quote(quote, self.reference)

    def test_float_input_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            eris_par_rate_bps(100.12, self.reference)  # type: ignore[arg-type]

    def test_nonpositive_pv01_is_rejected(self) -> None:
        bad = ErisReference(
            contract_id="YIT-TEST",
            fixed_rate_decimal=Decimal("0.04"),
            b_price_points=Decimal("0"),
            c_price_points=Decimal("0"),
            pv01_usd_per_bp=Decimal("0"),
            effective_date="2026-06-17",
            maturity_date="2028-06-21",
            observed_at=NOW,
        )
        with self.assertRaises(ValueError):
            eris_par_rate_bps(Decimal("100"), bad)


if __name__ == "__main__":
    unittest.main()
