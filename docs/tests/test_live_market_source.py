from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from data_pipeline.live_data_pipeline.eris_reference_data import CsvErisReferenceProvider
from data_pipeline.live_data_pipeline.live_market_source import (
    ContractRequest,
    IbkrLiveMarketSource,
    MarketDataError,
)


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)


@dataclass
class FakeContract:
    symbol: str
    exchange: str = "CBOT"
    currency: str = "USD"
    contractMonth: str = ""
    lastTradeDateOrContractMonth: str = ""
    conId: int = 0


@dataclass
class FakeDetail:
    contract: FakeContract
    minTick: float = 0.001


@dataclass
class FakeTicker:
    bid: float
    ask: float
    last: float
    time: datetime


class FakeIB:
    def __init__(self, *, stale=False, crossed=False):
        self.calls = []
        self.stale = stale
        self.crossed = crossed
        self.details = {
            "YIT": [
                FakeDetail(FakeContract("YIT", contractMonth="202607", lastTradeDateOrContractMonth="20280630", conId=101)),
                FakeDetail(FakeContract("YIT", contractMonth="202608", lastTradeDateOrContractMonth="20280831", conId=102)),
                FakeDetail(FakeContract("YIT", contractMonth="202609", lastTradeDateOrContractMonth="20280930", conId=103)),
            ],
            "YIW": [
                FakeDetail(FakeContract("YIW", contractMonth="202608", lastTradeDateOrContractMonth="20310831", conId=202)),
            ],
            "2YY": [
                FakeDetail(FakeContract("2YY", lastTradeDateOrContractMonth="20260930", conId=302)),
                FakeDetail(FakeContract("2YY", lastTradeDateOrContractMonth="20261231", conId=303)),
            ],
            "5YY": [
                FakeDetail(FakeContract("5YY", lastTradeDateOrContractMonth="20260930", conId=402)),
            ],
        }

    def reqContractDetails(self, contract):
        self.calls.append(("reqContractDetails", contract.symbol))
        return self.details[contract.symbol]

    def qualifyContracts(self, *contracts):
        self.calls.append(("qualifyContracts", tuple(c.symbol for c in contracts)))
        return list(contracts)

    def reqTickers(self, contract):
        self.calls.append(("reqTickers", contract.symbol))
        observed = NOW - timedelta(seconds=31) if self.stale else NOW
        bid, ask = (4.01, 4.00) if self.crossed else (3.99, 4.01)
        if contract.symbol in {"YIT", "YIW"}:
            bid, ask = ((100.01, 100.00) if self.crossed else (99.99, 100.01))
        return [FakeTicker(bid=bid, ask=ask, last=(bid + ask) / 2, time=observed)]

    def sleep(self, seconds):
        self.calls.append(("sleep", seconds))


def contract_factory(**kwargs):
    return FakeContract(**kwargs)


class LiveMarketSourceTests(unittest.TestCase):
    def test_resolves_expected_four_signal_symbols_and_never_orders(self) -> None:
        ib = FakeIB()
        source = IbkrLiveMarketSource(
            ib,
            contract_factory=contract_factory,
            quote_max_age_seconds=30,
        )
        requests = [
            ContractRequest("YIT", "eris"),
            ContractRequest("YIW", "eris"),
            ContractRequest("2YY", "treasury_yield"),
            ContractRequest("5YY", "treasury_yield"),
        ]
        quotes = source.snapshot(requests, now=NOW)

        self.assertEqual(set(quotes), {"YIT", "YIW", "2YY", "5YY"})
        requested = [value for name, value in ib.calls if name == "reqContractDetails"]
        self.assertEqual(requested, ["YIT", "YIW", "2YY", "5YY"])
        called_names = [name for name, *_ in ib.calls]
        self.assertNotIn("placeOrder", called_names)
        self.assertNotIn("reqGlobalCancel", called_names)
        self.assertNotIn("cancelOrder", called_names)
        self.assertNotIn("reqMktData", called_names)
        self.assertEqual(called_names.count("reqTickers"), 4)

        self.assertEqual(quotes["YIT"].contract_id, "102")
        self.assertEqual(quotes["2YY"].contract_id, "302")
        self.assertEqual(quotes["2YY"].mid_percent, Decimal("4.0"))

    def test_stale_quote_is_rejected(self) -> None:
        source = IbkrLiveMarketSource(
            FakeIB(stale=True),
            contract_factory=contract_factory,
            quote_max_age_seconds=30,
        )
        with self.assertRaisesRegex(MarketDataError, "stale"):
            source.snapshot([ContractRequest("YIT", "eris")], now=NOW)
        try:
            source.snapshot([ContractRequest("YIT", "eris")], now=NOW)
        except MarketDataError as exc:
            self.assertEqual(exc.reason_code, "stale_quote")

    def test_eligible_held_eris_contract_is_used_for_signal_quote(self) -> None:
        source = IbkrLiveMarketSource(
            FakeIB(),
            contract_factory=contract_factory,
            quote_max_age_seconds=30,
            preferred_con_ids={"YIT": 101},
        )

        quote = source.snapshot([ContractRequest("YIT", "eris")], now=NOW)["YIT"]

        self.assertEqual(quote.contract_id, "101")

    def test_crossed_quote_is_rejected(self) -> None:
        source = IbkrLiveMarketSource(
            FakeIB(crossed=True),
            contract_factory=contract_factory,
            quote_max_age_seconds=30,
        )
        with self.assertRaisesRegex(MarketDataError, "crossed"):
            source.snapshot([ContractRequest("2YY", "treasury_yield")], now=NOW)

    def test_csv_reference_provider_requires_exact_contract_and_fresh_row(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "eris_reference.csv"
            pd.DataFrame(
                [
                    {
                        "contract_id": "102",
                        "symbol": "YIT",
                        "fixed_rate_decimal": "0.04",
                        "b_price_points": "0.125",
                        "c_price_points": "0.025",
                        "pv01_usd_per_bp": "20",
                        "effective_date": "2026-06-17",
                        "maturity_date": "2028-06-21",
                        "observed_at": NOW.isoformat(),
                    }
                ]
            ).to_csv(path, index=False)
            provider = CsvErisReferenceProvider(path, max_age_seconds=3600)
            ref = provider.get("102", "YIT", NOW)
            self.assertEqual(ref.contract_id, "102")
            self.assertEqual(ref.pv01_usd_per_bp, Decimal("20"))

            with self.assertRaisesRegex(RuntimeError, "missing exact contract"):
                provider.get("999", "YIT", NOW)

    def test_reference_provider_rejects_stale_reference(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "eris_reference.csv"
            pd.DataFrame(
                [
                    {
                        "contract_id": "102",
                        "symbol": "YIT",
                        "fixed_rate_decimal": "0.04",
                        "b_price_points": "0.125",
                        "c_price_points": "0.025",
                        "pv01_usd_per_bp": "20",
                        "effective_date": "2026-06-17",
                        "maturity_date": "2028-06-21",
                        "observed_at": (NOW - timedelta(hours=2)).isoformat(),
                    }
                ]
            ).to_csv(path, index=False)
            provider = CsvErisReferenceProvider(path, max_age_seconds=3600)
            with self.assertRaisesRegex(RuntimeError, "stale"):
                provider.get("102", "YIT", NOW)


if __name__ == "__main__":
    unittest.main()
