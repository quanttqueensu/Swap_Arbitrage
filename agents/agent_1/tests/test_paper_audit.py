from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from agents.agent_1.models import (
    BoundContract, BrokerSnapshot, PositionAuditSnapshot, QuoteSnapshot,
)
from agents.agent_1.paper_audit import PaperAuditError, record_paper_audit


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, object]]]] = []

    def write(self, schema_id, rows):
        materialized = list(rows)
        self.calls.append((schema_id, materialized))
        return len(materialized)


class FakeIB:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.quote_contracts = []

    def reqTickers(self, *contracts):
        self.quote_contracts = list(contracts)
        result = []
        for contract in contracts:
            con_id = contract.conId
            result.append(SimpleNamespace(
                contract=contract,
                bid=Decimal("99") + con_id,
                ask=Decimal("99.01") + con_id,
                bidSize=Decimal("10"),
                askSize=Decimal("12"),
            ))
        return result

    def portfolio(self, account):
        return [
            SimpleNamespace(
                account=account,
                contract=SimpleNamespace(conId=1),
                position=2,
                averageCost=Decimal("98.5"),
                marketPrice=Decimal("99.5"),
                unrealizedPNL=Decimal("20"),
                realizedPNL=Decimal("5"),
            ),
            # Different instrument is ignored.
            SimpleNamespace(
                account=account,
                contract=SimpleNamespace(conId=999),
                position=1,
                averageCost=Decimal("1"),
                marketPrice=Decimal("1"),
                unrealizedPNL=Decimal("0"),
                realizedPNL=Decimal("0"),
            ),
        ]

    def fills(self):
        return [
            SimpleNamespace(
                contract=SimpleNamespace(conId=1),
                execution=SimpleNamespace(
                    orderId=101,
                    acctNumber="DU123",
                    side="BOT",
                    shares=2,
                    time=self.now,
                    price=Decimal("99.25"),
                    execId="fill-1",
                ),
                commissionReport=SimpleNamespace(commission=Decimal("1.25")),
            ),
            # Not an Agent 1 tracked order ID and must be ignored.
            SimpleNamespace(
                contract=SimpleNamespace(conId=1),
                execution=SimpleNamespace(
                    orderId=777,
                    acctNumber="DU123",
                    side="BOT",
                    shares=1,
                    time=self.now,
                    price=Decimal("99.25"),
                    execId="other-fill",
                ),
                commissionReport=SimpleNamespace(commission=Decimal("1")),
            ),
        ]


class PaperAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
        self.bindings = {
            "2Y:swap": BoundContract(
                "2Y", "swap", 1, "YIT", "YITM26", Decimal("0.01"), "ERIS-YIT-202606",
                broker_contract=SimpleNamespace(conId=1),
            ),
            "2Y:treasury": BoundContract(
                "2Y", "treasury", 2, "ZT", "ZTU26", Decimal("0.01"), "YAHOO-CONTINUOUS-ZT",
                broker_contract=SimpleNamespace(conId=2),
            ),
        }

    def test_records_canonical_quotes_positions_and_agent1_fills_without_account_values(self) -> None:
        store = FakeStore()
        summary = record_paper_audit(
            FakeIB(self.now),
            store,
            account_id="DU123",
            bindings=self.bindings,
            submitted_order_ids={"A1:2Y:abc:0001:SWAP": 101},
            observed_at=self.now,
        )

        self.assertEqual(summary, {"quotes": 2, "positions": 1, "fills": 1})
        by_schema = {schema: rows for schema, rows in store.calls}
        self.assertEqual(set(by_schema), {"paper_quotes", "paper_positions", "paper_fills"})

        self.assertEqual(set(by_schema["paper_quotes"][0]), {
            "timestamp_utc", "instrument_id", "bid_price", "ask_price", "bid_size", "ask_size",
        })
        self.assertEqual(set(by_schema["paper_positions"][0]), {
            "timestamp_utc", "instrument_id", "quantity", "average_cost", "market_price",
            "unrealized_pnl_usd", "realized_pnl_usd",
        })
        self.assertEqual(set(by_schema["paper_fills"][0]), {
            "fill_id", "order_ref", "fill_time_utc", "instrument_id", "side", "quantity",
            "fill_price", "commission_usd",
        })
        self.assertEqual(by_schema["paper_fills"][0]["order_ref"], "A1:2Y:abc:0001:SWAP")
        self.assertEqual(by_schema["paper_fills"][0]["quantity"], 2)
        self.assertNotIn("DU123", repr(store.calls))

    def test_reuses_cycle_snapshot_for_quotes_and_positions(self) -> None:
        ib = FakeIB(self.now)
        ib.reqTickers = lambda *contracts: (_ for _ in ()).throw(AssertionError("duplicate quote request"))
        ib.portfolio = lambda account: (_ for _ in ()).throw(AssertionError("duplicate portfolio request"))
        snapshot = BrokerSnapshot(
            observed_at=self.now,
            positions={1: 2, 2: 0},
            working_orders=(),
            quotes={
                1: QuoteSnapshot(1, Decimal("100"), Decimal("100.01"), self.now, Decimal("10"), Decimal("12")),
                2: QuoteSnapshot(2, Decimal("101"), Decimal("101.01"), self.now, Decimal("11"), Decimal("13")),
            },
            position_details={
                1: PositionAuditSnapshot(
                    quantity=2, average_cost=Decimal("98.5"), market_price=Decimal("99.5"),
                    unrealized_pnl_usd=Decimal("20"), realized_pnl_usd=Decimal("5"),
                )
            },
        )
        store = FakeStore()

        summary = record_paper_audit(
            ib, store, account_id="DU123", bindings=self.bindings,
            submitted_order_ids={"A1:2Y:abc:0001:SWAP": 101},
            observed_at=self.now, snapshot=snapshot,
        )

        self.assertEqual(summary, {"quotes": 2, "positions": 1, "fills": 1})

    def test_sell_fill_is_stored_with_negative_quantity(self) -> None:
        ib = FakeIB(self.now)
        fill = ib.fills()[0]
        fill.execution.side = "SLD"
        ib.fills = lambda: [fill]
        store = FakeStore()
        record_paper_audit(
            ib, store, account_id="DU123", bindings=self.bindings,
            submitted_order_ids={"A1:2Y:abc:0001:SWAP": 101}, observed_at=self.now,
        )
        fill_rows = next(rows for schema, rows in store.calls if schema == "paper_fills")
        self.assertEqual(fill_rows[0]["quantity"], -2)
        self.assertEqual(fill_rows[0]["side"], "SELL")

    def test_fill_from_other_account_is_ignored_even_when_order_id_matches(self) -> None:
        ib = FakeIB(self.now)
        fill = ib.fills()[0]
        fill.execution.acctNumber = "DU999"
        ib.fills = lambda: [fill]
        store = FakeStore()
        summary = record_paper_audit(
            ib, store, account_id="DU123", bindings=self.bindings,
            submitted_order_ids={"A1:2Y:abc:0001:SWAP": 101}, observed_at=self.now,
        )
        self.assertEqual(summary["fills"], 0)
        self.assertFalse(any(schema == "paper_fills" for schema, _ in store.calls))

    def test_invalid_quote_size_fails_closed_before_any_store_write(self) -> None:
        ib = FakeIB(self.now)
        original = ib.reqTickers
        def crossed(*contracts):
            rows = original(*contracts)
            rows[0].bidSize = Decimal("0")
            return rows
        ib.reqTickers = crossed
        store = FakeStore()
        with self.assertRaises(PaperAuditError):
            record_paper_audit(
                ib, store, account_id="DU123", bindings=self.bindings,
                submitted_order_ids={}, observed_at=self.now,
            )
        self.assertEqual(store.calls, [])


if __name__ == "__main__":
    unittest.main()
