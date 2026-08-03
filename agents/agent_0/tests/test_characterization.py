from __future__ import annotations

import argparse
import asyncio
import socket
import unittest
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import ib_insync

# ib_insync/eventkit creates an asyncio self-pipe during its first import.
# Complete only that documented local initialization before installing the
# module-lifetime guard. Agent 0 production imports happen below, after the
# guard proves it is active, and the guard stays active through every test.
_IB_MARKET_ORDER_CLASS = ib_insync.MarketOrder


def _blocked_network(*args, **kwargs):
    raise AssertionError("real network access is forbidden in Agent 0 tests")


_NETWORK_GUARD_PATCHERS = [
    patch.object(socket.socket, "connect", side_effect=_blocked_network),
    patch.object(socket.socket, "connect_ex", side_effect=_blocked_network),
    patch.object(socket.socket, "sendto", side_effect=_blocked_network),
    patch("socket.create_connection", side_effect=_blocked_network),
    patch("socket.getaddrinfo", side_effect=_blocked_network),
    patch.object(
        asyncio.BaseEventLoop,
        "create_connection",
        side_effect=_blocked_network,
    ),
]

for _network_guard_patcher in _NETWORK_GUARD_PATCHERS:
    _network_guard_patcher.start()


def _network_guard_is_active() -> bool:
    with socket.socket() as client:
        try:
            client.connect(("127.0.0.1", 7497))
        except AssertionError as exc:
            return "network access is forbidden" in str(exc)

    return False


_AGENT_IMPORT_GUARD_ACTIVE = _network_guard_is_active()
if not _AGENT_IMPORT_GUARD_ACTIVE:
    raise RuntimeError("Agent 0 test network guard was not active before import")

try:
    from agents.agent_0 import broker, config, run
    from agents.agent_0.contracts import allowed_instruments
    from agents.agent_0.models import AgentInstrument, QueuedOrder, SizingCap
    from agents.agent_0.orders import (
        build_order,
        load_orders,
        roll_tracking,
        save_orders,
    )
    from agents.agent_0.random_policy import RandomPolicy, next_weekdays
except BaseException:
    for _network_guard_patcher in reversed(_NETWORK_GUARD_PATCHERS):
        _network_guard_patcher.stop()
    raise


PAPER_ACCOUNT = "DU_TEST"
NEW_YORK = ZoneInfo("America/New_York")


def tearDownModule() -> None:
    for network_guard_patcher in reversed(_NETWORK_GUARD_PATCHERS):
        network_guard_patcher.stop()


def queued_order(
    order_ref: str,
    *,
    symbol: str = "ZT",
    side: str = "BUY",
    quantity: int = 1,
    status: str = "planned",
    contract_id: str = "",
    order_id: str = "",
) -> QueuedOrder:
    return QueuedOrder(
        order_ref=order_ref,
        activate_at=datetime(2026, 8, 3, 9, tzinfo=NEW_YORK),
        symbol=symbol,
        side=side,
        quantity=quantity,
        status=status,
        contract_id=contract_id,
        order_id=order_id,
    )


def fake_trade(
    order_ref: str,
    *,
    account: str = PAPER_ACCOUNT,
    action: str = "BUY",
    con_id: int = 101,
    order_id: int = 1,
    status: str = "PreSubmitted",
) -> SimpleNamespace:
    return SimpleNamespace(
        contract=SimpleNamespace(conId=con_id),
        order=SimpleNamespace(
            account=account,
            action=action,
            orderRef=order_ref,
            orderId=order_id,
        ),
        orderStatus=SimpleNamespace(status=status),
        log=[],
    )


class FakeIB:
    def __init__(
        self,
        *,
        managed_accounts: tuple[str, ...] = (PAPER_ACCOUNT,),
        open_trades: tuple[SimpleNamespace, ...] = (),
        margin_passes=lambda quantity: True,
    ) -> None:
        self._connected = False
        self._managed_accounts = list(managed_accounts)
        self.open_trades = list(open_trades)
        self.margin_passes = margin_passes
        self.connect_arguments: list[dict[str, object]] = []
        self.disconnect_count = 0
        self.global_cancel_count = 0
        self.previewed_quantities: list[int] = []
        self.placed: list[tuple[object, object]] = []
        self.sleep_calls: list[float] = []

    def connect(self, **kwargs):
        self.connect_arguments.append(kwargs)
        self._connected = True

    def isConnected(self):
        return self._connected

    def disconnect(self):
        self.disconnect_count += 1
        self._connected = False

    def managedAccounts(self):
        return list(self._managed_accounts)

    def reqAllOpenOrders(self):
        return list(self.open_trades)

    def reqGlobalCancel(self):
        self.global_cancel_count += 1
        self.open_trades.clear()

    def whatIfOrder(self, contract, order):
        quantity = int(order.totalQuantity)
        self.previewed_quantities.append(quantity)
        initial_margin = "80000" if self.margin_passes(quantity) else "90001"
        return SimpleNamespace(
            equityWithLoanAfter="100000",
            initMarginAfter=initial_margin,
        )

    def placeOrder(self, contract, order):
        order.orderId = 1000 + len(self.placed) + 1
        self.placed.append((contract, order))
        return SimpleNamespace(
            contract=contract,
            order=order,
            orderStatus=SimpleNamespace(status="PreSubmitted"),
            log=[],
        )

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)


class SocketGuardedTestCase(unittest.TestCase):
    """Every test in this module fails closed on a real network attempt."""

    def setUp(self) -> None:
        super().setUp()
        self.assertTrue(
            _network_guard_is_active(),
            "module-lifetime network guard must remain active through each test",
        )


class PaperRoutingTests(SocketGuardedTestCase):
    def test_agent_modules_are_imported_under_already_active_guard(self):
        self.assertTrue(_AGENT_IMPORT_GUARD_ACTIVE)

    def test_missing_and_non_paper_accounts_are_rejected(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Missing paper account"):
                config.get_agent_account_id()

        with self.assertRaisesRegex(RuntimeError, "refuses account"):
            config.assert_paper_only_settings("U_TEST")

    def test_paper_flags_and_port_fail_closed(self):
        mutations = (
            ("PAPER_ONLY", False),
            ("LIVE_TRADING_ENABLED", True),
            ("IBKR_PORT", 7496),
        )

        for setting, unsafe_value in mutations:
            with self.subTest(setting=setting):
                with patch.object(config, setting, unsafe_value):
                    with self.assertRaises(RuntimeError):
                        config.assert_paper_only_settings(PAPER_ACCOUNT)

    def test_connection_uses_only_current_paper_host_port_and_client(self):
        instances: list[FakeIB] = []

        class RoutedIB(FakeIB):
            def __init__(self):
                super().__init__()
                instances.append(self)

        self.assertTrue(config.PAPER_ONLY)
        self.assertFalse(config.LIVE_TRADING_ENABLED)
        self.assertEqual(config.IBKR_HOST, "127.0.0.1")
        self.assertEqual(config.IBKR_PORT, 7497)
        self.assertEqual(config.IBKR_CLIENT_ID, 30)

        with patch.object(broker, "_load_ib_class", return_value=RoutedIB):
            connected = broker.connect(PAPER_ACCOUNT)

        self.assertIs(connected, instances[0])
        self.assertEqual(
            connected.connect_arguments,
            [
                {
                    "host": "127.0.0.1",
                    "port": 7497,
                    "clientId": 30,
                    "timeout": 20,
                }
            ],
        )

    def test_unsafe_port_is_rejected_before_a_client_is_constructed(self):
        with patch.object(config, "IBKR_PORT", 7496):
            with patch.object(
                broker,
                "_load_ib_class",
                side_effect=AssertionError("client construction must not occur"),
            ):
                with self.assertRaisesRegex(RuntimeError, "paper port 7497"):
                    broker.connect(PAPER_ACCOUNT)

    def test_disabled_du_policy_is_rejected_before_ib_class_loading(self):
        with patch.object(config, "REQUIRE_PAPER_ACCOUNT_PREFIX", False):
            with self.assertRaisesRegex(RuntimeError, "DU paper-account policy"):
                config.assert_paper_only_settings(PAPER_ACCOUNT)

            with patch.object(
                broker,
                "_load_ib_class",
                side_effect=AssertionError("IB class loading must not occur"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "DU paper-account policy",
                ):
                    broker.connect(PAPER_ACCOUNT)

    def test_changed_du_prefix_is_rejected_before_ib_class_loading(self):
        with patch.object(config, "PAPER_ACCOUNT_PREFIX", "U"):
            with self.assertRaisesRegex(RuntimeError, "DU paper-account policy"):
                config.assert_paper_only_settings("U_TEST")

            with patch.object(
                broker,
                "_load_ib_class",
                side_effect=AssertionError("IB class loading must not occur"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "DU paper-account policy",
                ):
                    broker.connect("U_TEST")

    def test_managed_account_and_order_account_must_match(self):
        broker.validate_managed_account(FakeIB(), PAPER_ACCOUNT)

        with self.assertRaisesRegex(RuntimeError, "not visible"):
            broker.validate_managed_account(
                FakeIB(managed_accounts=("DU_OTHER",)),
                PAPER_ACCOUNT,
            )

        ib = FakeIB()
        order = build_order(PAPER_ACCOUNT, queued_order("account-check"))
        order.account = "DU_OTHER"

        with self.assertRaisesRegex(RuntimeError, "does not match"):
            broker.submit_order(ib, PAPER_ACCOUNT, SimpleNamespace(conId=1), order)

        self.assertEqual(ib.placed, [])

    def test_socket_guard_blocks_direct_connection_attempts(self):
        with socket.socket() as client:
            with self.assertRaisesRegex(AssertionError, "network access"):
                client.connect(("127.0.0.1", 7497))


class RandomPlanTests(SocketGuardedTestCase):
    def sizing_caps(self) -> dict[str, SizingCap]:
        return {
            instrument.symbol: SizingCap(
                instrument=instrument,
                main_quantity=100,
                max_agent_quantity=index + 2,
                source="test",
            )
            for index, instrument in enumerate(allowed_instruments())
        }

    def test_allowed_instruments_are_derived_from_current_configuration(self):
        actual = {
            (item.maturity, item.symbol, item.kind)
            for item in allowed_instruments()
        }

        self.assertEqual(
            actual,
            {
                ("2Y", "YIT", "swap_future"),
                ("5Y", "YIW", "swap_future"),
                ("2Y", "ZT", "treasury_future"),
                ("5Y", "ZF", "treasury_future"),
            },
        )

    def test_next_week_has_exactly_monday_through_friday(self):
        self.assertEqual(
            next_weekdays(date(2026, 7, 29)),
            [
                date(2026, 8, 3),
                date(2026, 8, 4),
                date(2026, 8, 5),
                date(2026, 8, 6),
                date(2026, 8, 7),
            ],
        )

    def test_authoritative_plan_has_five_orders_per_day_and_25_per_week(self):
        caps = self.sizing_caps()
        first = RandomPolicy(seed="p02-seed").build_week_plan(
            caps,
            date(2026, 7, 29),
        )
        second = RandomPolicy(seed="p02-seed").build_week_plan(
            caps,
            date(2026, 7, 29),
        )
        per_day = Counter(item.activate_at.date() for item in first)

        self.assertEqual(first, second)
        self.assertEqual(config.ORDERS_PER_DAY, 5)
        self.assertEqual(len(per_day), 5)
        self.assertEqual(sum(per_day.values()), 5 * 5)
        self.assertEqual(len(first), 25)
        self.assertEqual(
            per_day,
            Counter(
                {
                    date(2026, 8, 3): 5,
                    date(2026, 8, 4): 5,
                    date(2026, 8, 5): 5,
                    date(2026, 8, 6): 5,
                    date(2026, 8, 7): 5,
                }
            ),
        )

    def test_plan_fields_stay_within_current_symbol_side_quantity_and_time_bounds(self):
        caps = self.sizing_caps()
        plan = RandomPolicy(seed="p02-seed").build_week_plan(
            caps,
            date(2026, 7, 29),
        )
        max_quantity = {
            symbol: cap.max_agent_quantity for symbol, cap in caps.items()
        }

        self.assertEqual({item.side for item in plan}, {"BUY", "SELL"})
        self.assertTrue({item.symbol for item in plan} <= set(max_quantity))
        self.assertEqual(len({item.order_ref for item in plan}), 25)

        for item in plan:
            seconds = (
                item.activate_at.hour * 3600
                + item.activate_at.minute * 60
                + item.activate_at.second
            )
            self.assertEqual(item.activate_at.tzinfo.key, "America/New_York")
            self.assertGreaterEqual(seconds, 9 * 3600)
            self.assertLessEqual(seconds, 15 * 3600)
            self.assertGreaterEqual(item.quantity, 1)
            self.assertLessEqual(item.quantity, max_quantity[item.symbol])
            self.assertRegex(
                item.order_ref,
                r"^agent_0-2026080[3-7]-0[1-5]$",
            )

    def test_scheduled_market_order_preserves_account_reference_and_utc_activation(self):
        record = queued_order("agent_0-20260803-01", quantity=2)
        order = build_order(PAPER_ACCOUNT, record)

        self.assertEqual(order.orderType, "MKT")
        self.assertEqual(order.tif, "DAY")
        self.assertEqual(order.account, PAPER_ACCOUNT)
        self.assertEqual(order.orderRef, "agent_0-20260803-01")
        self.assertEqual(order.goodAfterTime, "20260803-13:00:00")
        self.assertTrue(order.transmit)


class CapacityAndMarginTests(SocketGuardedTestCase):
    def test_three_contract_allocation_has_independent_fifteen_order_side_buckets(self):
        buy_records = [
            queued_order(f"buy-allocation-{index}", side="BUY")
            for index in range(45)
        ]
        sell_records = [
            queued_order(f"sell-allocation-{index}", side="SELL")
            for index in range(45)
        ]
        records = [*buy_records, *sell_records]
        contracts = [SimpleNamespace(conId=value) for value in (1, 2, 3)]

        self.assertEqual(config.CONTRACTS_PER_SYMBOL, 3)
        allocated = run.allocate_contracts(
            records,
            {"ZT": contracts},
            Counter(),
        )
        counts = Counter(
            (allocated[record.order_ref].conId, record.side)
            for record in records
        )

        self.assertEqual(
            counts,
            Counter(
                {
                    (1, "BUY"): 15,
                    (2, "BUY"): 15,
                    (3, "BUY"): 15,
                    (1, "SELL"): 15,
                    (2, "SELL"): 15,
                    (3, "SELL"): 15,
                }
            ),
        )
        self.assertLessEqual(max(counts.values()), 15)

    def test_existing_account_orders_consume_capacity_but_other_accounts_do_not(self):
        trades = [
            fake_trade(f"own-{index}", con_id=1)
            for index in range(15)
        ]
        trades.append(
            fake_trade(
                "other-account",
                account="DU_OTHER",
                con_id=2,
            )
        )
        occupied = run.working_order_counts(trades, PAPER_ACCOUNT)
        record = queued_order("next")
        contracts = [SimpleNamespace(conId=value) for value in (1, 2, 3)]

        allocated = run.allocate_contracts(
            [record],
            {"ZT": contracts},
            occupied,
        )

        self.assertEqual(occupied, Counter({("1", "BUY"): 15}))
        self.assertEqual(allocated["next"].conId, 2)

    def test_capacity_failure_occurs_before_any_order_submission(self):
        records = [queued_order(f"capacity-{index}") for index in range(46)]
        ib = FakeIB()
        contracts = [SimpleNamespace(conId=value) for value in (1, 2, 3)]

        with TemporaryDirectory() as directory:
            upcoming = Path(directory) / "upcoming.csv"
            previous = Path(directory) / "previous.csv"

            with patch.object(run, "resolve_futures", return_value=contracts):
                with self.assertRaisesRegex(RuntimeError, "working-order capacity"):
                    run.submit_plan(
                        ib,
                        PAPER_ACCOUNT,
                        records,
                        upcoming,
                        previous,
                    )

        self.assertEqual(ib.previewed_quantities, [])
        self.assertEqual(ib.placed, [])

    def test_margin_reserve_uses_exact_ten_percent_boundary_and_fails_closed(self):
        self.assertTrue(
            run.margin_reserve_ok(
                SimpleNamespace(
                    equityWithLoanAfter="100000",
                    initMarginAfter="90000",
                )
            )
        )
        self.assertFalse(
            run.margin_reserve_ok(
                SimpleNamespace(
                    equityWithLoanAfter="100000",
                    initMarginAfter="90001",
                )
            )
        )
        self.assertFalse(run.margin_reserve_ok(SimpleNamespace()))
        self.assertFalse(
            run.margin_reserve_ok(
                SimpleNamespace(
                    equityWithLoanAfter="not-a-number",
                    initMarginAfter="0",
                )
            )
        )

    def test_margin_preview_reduces_to_largest_passing_quantity(self):
        ib = FakeIB(margin_passes=lambda quantity: quantity <= 2)
        record = queued_order("margin-reduce", quantity=4)

        order = run.fit_order_to_margin(
            ib,
            PAPER_ACCOUNT,
            SimpleNamespace(conId=1),
            record,
        )

        self.assertIsNotNone(order)
        self.assertEqual(ib.previewed_quantities, [4, 3, 2])
        self.assertEqual(record.quantity, 2)
        self.assertEqual(int(order.totalQuantity), 2)
        self.assertEqual(ib.placed, [])

    def test_quantity_one_margin_failure_never_calls_place_order(self):
        ib = FakeIB(margin_passes=lambda quantity: False)
        record = queued_order("margin-block", quantity=1)

        with TemporaryDirectory() as directory:
            upcoming = Path(directory) / "upcoming.csv"
            previous = Path(directory) / "previous.csv"
            contracts = [SimpleNamespace(conId=value) for value in (1, 2, 3)]

            with patch.object(run, "resolve_futures", return_value=contracts):
                summary = run.submit_plan(
                    ib,
                    PAPER_ACCOUNT,
                    [record],
                    upcoming,
                    previous,
                )

            saved = load_orders(upcoming)

        self.assertEqual(summary["margin_blocked"], 1)
        self.assertEqual(ib.previewed_quantities, [1])
        self.assertEqual(ib.placed, [])
        self.assertEqual(saved[0].status, "planned")


class ReconciliationTests(SocketGuardedTestCase):
    def test_plan_reconciliation_preserves_matching_state_and_drops_obsolete_rows(self):
        matching = queued_order(
            "keep",
            status="accepted",
            contract_id="55",
            order_id="77",
        )
        obsolete = queued_order("obsolete", status="accepted")
        proposed = [queued_order("keep"), queued_order("new")]

        reconciled = run.reconcile_plan([matching, obsolete], proposed)

        self.assertEqual([item.order_ref for item in reconciled], ["keep", "new"])
        self.assertIs(reconciled[0], matching)
        self.assertEqual(reconciled[0].status, "accepted")
        self.assertEqual(reconciled[0].order_id, "77")
        self.assertNotIn(obsolete, reconciled)

    def test_server_reference_is_authoritative_and_suppresses_duplicate_submission(self):
        record = queued_order("server-authoritative")
        server_trade = fake_trade(
            record.order_ref,
            con_id=88,
            order_id=42,
        )
        ib = FakeIB(open_trades=(server_trade,))

        with TemporaryDirectory() as directory:
            upcoming = Path(directory) / "upcoming.csv"
            previous = Path(directory) / "previous.csv"
            summary = run.submit_plan(
                ib,
                PAPER_ACCOUNT,
                [record],
                upcoming,
                previous,
            )
            saved = load_orders(upcoming)

        self.assertEqual(summary["already_submitted"], 1)
        self.assertEqual(summary["accepted"], 0)
        self.assertEqual(ib.placed, [])
        self.assertEqual(saved[0].status, "accepted")
        self.assertEqual(saved[0].order_id, "42")
        self.assertEqual(saved[0].contract_id, "88")

    def test_local_acceptance_missing_from_server_is_reset_and_resubmitted(self):
        record = queued_order(
            "missing-server",
            status="accepted",
            contract_id="55",
            order_id="77",
        )
        ib = FakeIB()
        contracts = [SimpleNamespace(conId=value) for value in (1, 2, 3)]

        with TemporaryDirectory() as directory:
            upcoming = Path(directory) / "upcoming.csv"
            previous = Path(directory) / "previous.csv"

            with patch.object(run, "resolve_futures", return_value=contracts):
                summary = run.submit_plan(
                    ib,
                    PAPER_ACCOUNT,
                    [record],
                    upcoming,
                    previous,
                )

            saved = load_orders(upcoming)

        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["already_submitted"], 0)
        self.assertEqual(len(ib.placed), 1)
        self.assertEqual(saved[0].status, "accepted")
        self.assertEqual(saved[0].order_id, "1001")
        self.assertEqual(saved[0].contract_id, "1")


class CancellationTests(SocketGuardedTestCase):
    def test_normal_queue_uses_fake_broker_and_never_invokes_global_cancel(self):
        fixed_now = datetime(2026, 7, 29, 16, tzinfo=timezone.utc)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz is None else fixed_now.astimezone(tz)

        instrument = AgentInstrument("2Y", "ZT", "treasury_future")
        caps = {
            "ZT": SizingCap(
                instrument,
                main_quantity=100,
                max_agent_quantity=3,
                source="test",
            )
        }
        ib = FakeIB()
        contracts = [SimpleNamespace(conId=value) for value in (1, 2, 3)]
        actual_roll_tracking = roll_tracking
        actual_submit_plan = run.submit_plan

        with TemporaryDirectory() as directory:
            upcoming = Path(directory) / "upcoming.csv"
            previous = Path(directory) / "previous.csv"

            def safe_roll(now):
                actual_roll_tracking(now, upcoming, previous)

            def safe_submit(ib_client, account_id, records):
                return actual_submit_plan(
                    ib_client,
                    account_id,
                    records,
                    upcoming,
                    previous,
                )

            with (
                patch.object(run, "datetime", FixedDateTime),
                patch.object(run.config, "UPCOMING_ORDERS_FILE", upcoming),
                patch.object(run.config, "PREVIOUS_ORDERS_FILE", previous),
                patch.object(run, "roll_tracking", side_effect=safe_roll),
                patch.object(run, "submit_plan", side_effect=safe_submit),
                patch.object(run, "load_sizing_caps", return_value=caps),
                patch.object(run, "connect", return_value=ib),
                patch.object(run, "disconnect", side_effect=lambda item: item.disconnect()),
                patch.object(run, "resolve_futures", return_value=contracts),
                patch.object(
                    run,
                    "cancel_all_orders",
                    side_effect=AssertionError("normal queue must not cancel"),
                ),
                patch.dict(
                    "os.environ",
                    {config.RANDOM_SEED_ENV_VAR: "p02-normal-queue"},
                    clear=False,
                ),
            ):
                summary = run.queue_next_week(PAPER_ACCOUNT)

            saved = load_orders(upcoming)

        self.assertEqual(summary["planned"], 25)
        self.assertEqual(summary["accepted"], 25)
        self.assertEqual(len(ib.placed), 25)
        self.assertEqual(ib.global_cancel_count, 0)
        self.assertEqual(len(saved), 25)
        self.assertEqual({item.status for item in saved}, {"accepted"})

    def test_explicit_cancel_cli_is_separate_but_session_wide(self):
        visible_orders = (
            fake_trade("agent-order", account=PAPER_ACCOUNT),
            fake_trade("other-account-order", account="DU_OTHER"),
            fake_trade("manual-order", account=""),
        )
        ib = FakeIB(open_trades=visible_orders)
        actual_cancel = run.cancel_all_working_orders

        with TemporaryDirectory() as directory:
            upcoming = Path(directory) / "upcoming.csv"
            save_orders(
                upcoming,
                [
                    queued_order(
                        "agent-order",
                        status="accepted",
                        contract_id="101",
                        order_id="1",
                    )
                ],
            )

            def safe_cancel(account_id):
                return actual_cancel(account_id, upcoming)

            with (
                patch.object(
                    run,
                    "parse_args",
                    return_value=argparse.Namespace(
                        account=PAPER_ACCOUNT,
                        cancel_all=True,
                    ),
                ),
                patch.object(run, "connect", return_value=ib),
                patch.object(run, "disconnect", side_effect=lambda item: item.disconnect()),
                patch.object(
                    run,
                    "cancel_all_working_orders",
                    side_effect=safe_cancel,
                ),
                patch.object(
                    run,
                    "queue_next_week",
                    side_effect=AssertionError("cancel CLI must not queue"),
                ),
            ):
                run.main()

            saved = load_orders(upcoming)

        self.assertEqual(ib.global_cancel_count, 1)
        self.assertEqual(ib.open_trades, [])
        self.assertEqual(saved[0].status, "planned")
        self.assertEqual(saved[0].contract_id, "")
        self.assertEqual(saved[0].order_id, "")


if __name__ == "__main__":
    unittest.main()
