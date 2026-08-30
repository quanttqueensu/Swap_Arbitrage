from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from agents.agent_1.broker import (
    BrokerError,
    assess_quotes,
    collect_broker_snapshot,
    validate_paper_session,
)
from agents.agent_1.models import BoundContract


def contract(con_id: int, symbol: str):
    return SimpleNamespace(conId=con_id, symbol=symbol, localSymbol=f"{symbol}X")


def open_trade(
    *,
    con_id: int,
    ref: str,
    account: str,
    client_id: int,
    action: str,
    remaining: float,
    order_id: int,
    status: str = "Submitted",
):
    return SimpleNamespace(
        contract=contract(con_id, "X"),
        order=SimpleNamespace(
            orderRef=ref,
            account=account,
            clientId=client_id,
            action=action,
            orderId=order_id,
        ),
        orderStatus=SimpleNamespace(status=status, remaining=remaining),
    )


class FakeIB:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.connected = True
        self.accounts = ["DU123"]
        self._positions = [
            SimpleNamespace(account="DU123", contract=contract(101, "YIT"), position=8.0),
            SimpleNamespace(account="DU123", contract=contract(102, "ZT"), position=-5.0),
            SimpleNamespace(account="DU999", contract=contract(101, "YIT"), position=99.0),
        ]
        self._orders = [
            open_trade(
                con_id=101,
                ref="A1:2Y:g1:SWAP",
                account="DU123",
                client_id=31,
                action="BUY",
                remaining=2,
                order_id=1,
            ),
            open_trade(
                con_id=102,
                ref="A1:2Y:g1:TREASURY",
                account="DU123",
                client_id=31,
                action="SELL",
                remaining=1,
                order_id=2,
            ),
            open_trade(
                con_id=101,
                ref="agent_0-foo",
                account="DU123",
                client_id=30,
                action="BUY",
                remaining=50,
                order_id=3,
            ),
        ]

    def isConnected(self):
        return self.connected

    def managedAccounts(self):
        return list(self.accounts)

    def positions(self):
        return list(self._positions)

    def reqAllOpenOrders(self):
        return list(self._orders)

    def reqTickers(self, *contracts):
        return [
            SimpleNamespace(
                contract=item,
                bid=Decimal("99.00") if item.conId == 101 else Decimal("102.00"),
                ask=Decimal("99.01") if item.conId == 101 else Decimal("102.02"),
                time=self.now,
            )
            for item in contracts
        ]

    def fills(self):
        return []


class BrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
        self.bindings = {
            "2Y:swap": BoundContract(
                maturity="2Y", leg="swap", con_id=101, symbol="YIT",
                local_symbol="YITM26", min_tick=Decimal("0.005"),
                risk_id="ERIS-YIT-202606", broker_contract=contract(101, "YIT"),
            ),
            "2Y:treasury": BoundContract(
                maturity="2Y", leg="treasury", con_id=102, symbol="ZT",
                local_symbol="ZTU26", min_tick=Decimal("0.0078125"),
                risk_id="YAHOO-CONTINUOUS-ZT", broker_contract=contract(102, "ZT"),
            ),
        }

    def test_validates_connected_managed_paper_session(self) -> None:
        validate_paper_session(FakeIB(self.now), account_id="DU123")

    def test_rejects_account_not_visible_to_session(self) -> None:
        with self.assertRaisesRegex(BrokerError, "managed"):
            validate_paper_session(FakeIB(self.now), account_id="DU999")

    def test_snapshot_uses_account_positions_and_agent1_working_quantity(self) -> None:
        snapshot = collect_broker_snapshot(
            FakeIB(self.now),
            account_id="DU123",
            client_id=31,
            bindings=self.bindings,
            observed_at=self.now,
        )

        self.assertEqual(snapshot.position_state(101).confirmed_qty, 8)
        self.assertEqual(snapshot.position_state(101).working_qty, 2)
        self.assertEqual(snapshot.position_state(102).confirmed_qty, -5)
        self.assertEqual(snapshot.position_state(102).working_qty, -1)
        self.assertEqual(len(snapshot.working_orders), 2)
        self.assertEqual(snapshot.quotes[101].bid, Decimal("99.00"))

    def test_quote_assessment_detects_stale_and_crossed_quotes(self) -> None:
        ib = FakeIB(self.now)
        snapshot = collect_broker_snapshot(
            ib,
            account_id="DU123",
            client_id=31,
            bindings=self.bindings,
            observed_at=self.now,
        )
        fresh = assess_quotes(snapshot, now=self.now, max_age_seconds=Decimal("10"))
        self.assertTrue(fresh.data_fresh)
        self.assertTrue(fresh.bid_ask_valid)
        self.assertTrue(fresh.market_fields_valid)

        stale_quote = SimpleNamespace(**snapshot.quotes[101].__dict__)
        stale_quote.timestamp = self.now - timedelta(seconds=11)
        crossed_quote = SimpleNamespace(**snapshot.quotes[102].__dict__)
        crossed_quote.bid = Decimal("103")
        crossed_quote.ask = Decimal("102")
        modified = snapshot.with_quotes({101: stale_quote, 102: crossed_quote})

        assessed = assess_quotes(modified, now=self.now, max_age_seconds=Decimal("10"))
        self.assertFalse(assessed.data_fresh)
        self.assertFalse(assessed.bid_ask_valid)
        self.assertTrue(assessed.market_fields_valid)

    def test_fractional_tracked_futures_position_fails_closed(self) -> None:
        ib = FakeIB(self.now)
        ib._positions[0].position = 1.5
        with self.assertRaisesRegex(BrokerError, "integer"):
            collect_broker_snapshot(
                ib,
                account_id="DU123",
                client_id=31,
                bindings=self.bindings,
                observed_at=self.now,
            )


if __name__ == "__main__":
    unittest.main()
