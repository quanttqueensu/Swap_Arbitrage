from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from collections.abc import Callable, Iterable
from typing import Any
import re

from data_pipeline.paper_store import PaperEventStore


_ACCOUNT_ID = re.compile(r"(?:^|\b)(?:DU|U)\d{3,}(?:\b|$)", re.IGNORECASE)


class PaperSafetyError(RuntimeError):
    """Raised when a paper quote operation cannot be performed safely."""


@dataclass(frozen=True)
class PaperSessionConfig:
    host: str
    port: int
    client_id: int
    account_id: str
    account_alias: str
    paper_only: bool = True
    live_trading_enabled: bool = False
    stale_after_seconds: int = 30


def _redact(_: object) -> str:
    return "<redacted>"


def _instrument_id(contract: object) -> str:
    con_id = getattr(contract, "conId", None)
    if isinstance(con_id, bool) or not isinstance(con_id, int) or con_id <= 0:
        raise PaperSafetyError("contract must have a positive contract ID")
    return f"IBKR:{con_id}"


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperSafetyError("timestamp must include a timezone")
    utc_value = value.astimezone(timezone.utc)
    return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z").replace(".000000Z", "Z")


def _finite_decimal(value: object, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PaperSafetyError(f"{field_name} must be finite") from error
    if not decimal_value.is_finite():
        raise PaperSafetyError(f"{field_name} must be finite")
    if decimal_value <= 0:
        raise PaperSafetyError(f"{field_name} must be positive")
    return decimal_value


def _finite_nonnegative_decimal(value: object, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PaperSafetyError(f"{field_name} must be finite") from error
    if not decimal_value.is_finite():
        raise PaperSafetyError(f"{field_name} must be finite")
    if decimal_value < 0:
        raise PaperSafetyError(f"{field_name} must be nonnegative")
    return decimal_value


def _finite_value(value: object, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PaperSafetyError(f"{field_name} must be finite") from error
    if not decimal_value.is_finite():
        raise PaperSafetyError(f"{field_name} must be finite")
    return decimal_value


def _nonempty_text(value: object, field_name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise PaperSafetyError(f"nonempty {field_name} is required")
    return text


def _signed_quantity(side: object, quantity: object, field_name: str) -> tuple[str, int]:
    normalized_side = _nonempty_text(side, "side").upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise PaperSafetyError("side must be BUY or SELL")
    if isinstance(quantity, bool):
        raise PaperSafetyError(f"{field_name} must be a positive integer")
    try:
        unsigned_quantity = int(quantity)
    except (TypeError, ValueError) as error:
        raise PaperSafetyError(f"{field_name} must be a positive integer") from error
    if unsigned_quantity <= 0 or str(quantity).strip() not in {str(unsigned_quantity), f"+{unsigned_quantity}"}:
        raise PaperSafetyError(f"{field_name} must be a positive integer")
    return normalized_side, unsigned_quantity if normalized_side == "BUY" else -unsigned_quantity


def _reject_broker_account(value: object, seen: set[int] | None = None) -> None:
    if isinstance(value, str):
        if _ACCOUNT_ID.search(value) is not None:
            raise PaperSafetyError("broker account values are not permitted")
        return
    if value is None or isinstance(value, (int, float, Decimal, datetime, bool)):
        return
    seen = seen if seen is not None else set()
    value_id = id(value)
    if value_id in seen:
        return
    seen.add(value_id)
    if isinstance(value, dict):
        for item in value.values():
            _reject_broker_account(item, seen)
        return
    if isinstance(value, (tuple, list, set, frozenset)):
        for item in value:
            _reject_broker_account(item, seen)
        return
    try:
        attributes = vars(value)
    except TypeError:
        return
    for item in attributes.values():
        _reject_broker_account(item, seen)


class IbkrPaperRecorder:
    def __init__(
        self,
        ib: Any,
        config: PaperSessionConfig,
        store: PaperEventStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.ib = ib
        self.config = config
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def validate_session(self) -> None:
        if not isinstance(self.config.host, str) or self.config.host != "127.0.0.1":
            raise PaperSafetyError("unsafe paper host")
        if isinstance(self.config.port, bool) or not isinstance(self.config.port, int) or self.config.port != 7497:
            raise PaperSafetyError("unsafe paper port")
        if self.config.client_id != 30:
            raise PaperSafetyError("paper client ID is required")
        if self.config.paper_only is not True:
            raise PaperSafetyError("paper-only mode is required")
        if self.config.live_trading_enabled is not False:
            raise PaperSafetyError("live trading must be disabled")
        if not isinstance(self.config.account_id, str) or not self.config.account_id.startswith("DU"):
            raise PaperSafetyError("DU paper account is required")
        if not isinstance(self.config.account_alias, str) or not self.config.account_alias:
            raise PaperSafetyError("nonempty account alias is required")
        if (
            isinstance(self.config.stale_after_seconds, bool)
            or not isinstance(self.config.stale_after_seconds, int)
            or self.config.stale_after_seconds <= 0
        ):
            raise PaperSafetyError("positive stale quote limit is required")
        broker_failure = False
        connected = False
        accounts: tuple[object, ...] = ()
        try:
            connected = self.ib.isConnected()
            if connected:
                accounts = tuple(self.ib.managedAccounts())
        except Exception:
            broker_failure = True
        if broker_failure:
            raise PaperSafetyError("broker session validation failed")
        if not connected:
            raise PaperSafetyError("IBKR paper session is not connected")
        if self.config.account_id not in accounts:
            raise PaperSafetyError("configured managed account is unavailable")

    def request_quotes(self, contracts: list[object]) -> list[object]:
        self.validate_session()
        checked_contracts = list(contracts)
        for contract in checked_contracts:
            _instrument_id(contract)
        return [self.ib.reqMktData(contract, "", False, False) for contract in checked_contracts]

    def record_quote(self, contract: object, ticker: object, observed_at_utc: datetime) -> int:
        self.validate_session()
        instrument_id = _instrument_id(contract)
        timestamp_utc = _utc_text(observed_at_utc)
        now_utc = self.clock()
        _utc_text(now_utc)
        age_seconds = (now_utc.astimezone(timezone.utc) - observed_at_utc.astimezone(timezone.utc)).total_seconds()
        if age_seconds < 0 or age_seconds > self.config.stale_after_seconds:
            raise PaperSafetyError("stale quote timestamp")
        bid_price = _finite_decimal(getattr(ticker, "bid", None), "bid_price")
        ask_price = _finite_decimal(getattr(ticker, "ask", None), "ask_price")
        bid_size = _finite_decimal(getattr(ticker, "bidSize", None), "bid_size")
        ask_size = _finite_decimal(getattr(ticker, "askSize", None), "ask_size")
        if bid_price > ask_price:
            raise PaperSafetyError("crossed quote")
        return self.store.write(
            "paper_quotes",
            [{
                "timestamp_utc": timestamp_utc,
                "instrument_id": instrument_id,
                "bid_price": bid_price,
                "ask_price": ask_price,
                "bid_size": bid_size,
                "ask_size": ask_size,
            }],
        )

    def record_order(
        self, decision_id: str, contract: object, order: object, status: str, created_at_utc: datetime
    ) -> int:
        self.validate_session()
        _reject_broker_account(contract)
        _reject_broker_account(order)
        order_ref = _nonempty_text(getattr(order, "orderRef", None), "orderRef")
        normalized_decision_id = _nonempty_text(decision_id, "decision_id")
        normalized_status = _nonempty_text(status, "order status")
        side, quantity = _signed_quantity(getattr(order, "action", None), getattr(order, "totalQuantity", None), "quantity")
        broker_order_id = getattr(order, "orderId", None)
        try:
            ibkr_order_id = str(broker_order_id) if int(broker_order_id or 0) > 0 else ""
        except (TypeError, ValueError) as error:
            raise PaperSafetyError("broker order ID must be an integer") from error
        return self.store.write(
            "paper_orders",
            [{
                "order_ref": order_ref,
                "decision_id": normalized_decision_id,
                "created_at_utc": _utc_text(created_at_utc),
                "instrument_id": _instrument_id(contract),
                "side": side,
                "quantity": quantity,
                "order_type": _nonempty_text(getattr(order, "orderType", None), "order type"),
                "time_in_force": _nonempty_text(getattr(order, "tif", None), "time in force"),
                "status": normalized_status,
                "ibkr_order_id": ibkr_order_id,
            }],
        )

    def record_fill(
        self, order_ref: str, contract: object, execution: object, commission_report: object
    ) -> int:
        self.validate_session()
        _reject_broker_account(contract)
        _reject_broker_account(execution)
        _reject_broker_account(commission_report)
        if commission_report is None:
            raise PaperSafetyError("commission report is required")
        side, quantity = _signed_quantity(getattr(execution, "side", None), getattr(execution, "shares", None), "fill quantity")
        return self.store.write(
            "paper_fills",
            [{
                "fill_id": _nonempty_text(getattr(execution, "execId", None), "execution ID"),
                "order_ref": _nonempty_text(order_ref, "order_ref"),
                "fill_time_utc": _utc_text(getattr(execution, "time", None)),
                "instrument_id": _instrument_id(contract),
                "side": side,
                "quantity": quantity,
                "fill_price": _finite_decimal(getattr(execution, "price", None), "fill price"),
                "commission_usd": _finite_nonnegative_decimal(getattr(commission_report, "commission", None), "commission"),
            }],
        )

    def record_positions(self, position_rows: Iterable[object], observed_at_utc: datetime) -> int:
        self.validate_session()
        timestamp_utc = _utc_text(observed_at_utc)
        normalized_rows: list[dict[str, object]] = []
        instrument_ids: set[str] = set()
        for position in position_rows:
            _reject_broker_account(position)
            contract = getattr(position, "contract", None)
            _reject_broker_account(contract)
            instrument_id = _instrument_id(contract)
            if instrument_id in instrument_ids:
                raise PaperSafetyError("duplicate instrument in position snapshot")
            instrument_ids.add(instrument_id)
            quantity = getattr(position, "position", None)
            if isinstance(quantity, bool):
                raise PaperSafetyError("position quantity must be an integer")
            try:
                integer_quantity = int(quantity)
            except (TypeError, ValueError) as error:
                raise PaperSafetyError("position quantity must be an integer") from error
            if str(quantity).strip() not in {str(integer_quantity), f"+{integer_quantity}"}:
                raise PaperSafetyError("position quantity must be an integer")
            normalized_rows.append({
                "timestamp_utc": timestamp_utc,
                "instrument_id": instrument_id,
                "quantity": integer_quantity,
                "average_cost": _finite_decimal(getattr(position, "avgCost", None), "average cost"),
                "market_price": _finite_decimal(getattr(position, "marketPrice", None), "market price"),
                "unrealized_pnl_usd": _finite_value(getattr(position, "unrealizedPNL", None), "unrealized P&L"),
                "realized_pnl_usd": _finite_value(getattr(position, "realizedPNL", None), "realized P&L"),
            })
        return self.store.write("paper_positions", normalized_rows)
