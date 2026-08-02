from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
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


def _finite_decimal(value: Decimal, field_name: str) -> Decimal:
    if not value.is_finite():
        raise PaperSafetyError(f"{field_name} must be finite")
    if value <= 0:
        raise PaperSafetyError(f"{field_name} must be positive")
    return value


def _finite_nonnegative_decimal(value: Decimal, field_name: str) -> Decimal:
    if not value.is_finite():
        raise PaperSafetyError(f"{field_name} must be finite")
    if value < 0:
        raise PaperSafetyError(f"{field_name} must be nonnegative")
    return value


def _finite_value(value: Decimal, field_name: str) -> Decimal:
    if not value.is_finite():
        raise PaperSafetyError(f"{field_name} must be finite")
    return value


def _nonempty_text(value: object, field_name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise PaperSafetyError(f"nonempty {field_name} is required")
    return text


def _signed_quantity(side: object, quantity: int, field_name: str) -> tuple[str, int]:
    normalized_side = _nonempty_text(side, "side").upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise PaperSafetyError("side must be BUY or SELL")
    if quantity <= 0:
        raise PaperSafetyError(f"{field_name} must be a positive integer")
    return normalized_side, quantity if normalized_side == "BUY" else -quantity


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


def _normalize_broker_object[T](normalizer: Callable[[], T]) -> T:
    try:
        return normalizer()
    except Exception:
        pass
    raise PaperSafetyError("broker object normalization failed")


def _instrument_id_from_text(con_id: str) -> str:
    if not con_id.isascii() or not con_id.isdecimal() or int(con_id) <= 0:
        raise PaperSafetyError("contract must have a positive contract ID")
    return f"IBKR:{con_id}"


def _strict_broker_contract_id(contract: object) -> int | None:
    _reject_broker_account(contract)
    con_id = getattr(contract, "conId", None)
    return con_id if type(con_id) is int else None


def _instrument_id_from_int(con_id: int | None) -> str:
    if con_id is None or con_id <= 0:
        raise PaperSafetyError("contract must have a positive contract ID")
    return f"IBKR:{con_id}"


def _broker_text(value: object, field_name: str) -> str:
    value = getattr(value, field_name, None)
    return "" if value is None else str(value)


def _broker_integer(value: object, field_name: str, *, nullable: bool = False) -> int:
    text = _broker_text(value, field_name).strip()
    if nullable and not text:
        return 0
    integer = int(text)
    if text not in {str(integer), f"+{integer}"}:
        raise ValueError("noncanonical integer")
    return integer


def _broker_decimal(value: object, field_name: str) -> Decimal:
    return Decimal(_broker_text(value, field_name))


def _broker_contract_id(contract: object) -> str:
    con_id = _broker_text(contract, "conId")
    if not con_id.isascii() or not con_id.isdecimal():
        return ""
    normalized = int(con_id)
    return str(normalized) if normalized > 0 else ""


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
        if type(self.config.client_id) is not int or self.config.client_id != 30:
            raise PaperSafetyError("paper client ID is required")
        if self.config.paper_only is not True:
            raise PaperSafetyError("paper-only mode is required")
        if self.config.live_trading_enabled is not False:
            raise PaperSafetyError("live trading must be disabled")
        if not isinstance(self.config.account_id, str) or re.fullmatch(r"DU\d+", self.config.account_id) is None:
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
        contract_ids = [
            _normalize_broker_object(lambda contract=contract: _strict_broker_contract_id(contract))
            for contract in checked_contracts
        ]
        for con_id in contract_ids:
            _instrument_id_from_int(con_id)
        return [
            _normalize_broker_object(
                lambda contract=contract: self.ib.reqMktData(contract, "", False, False)
            )
            for contract in checked_contracts
        ]

    def record_quote(self, contract: object, ticker: object, observed_at_utc: datetime) -> int:
        self.validate_session()
        timestamp_utc = _utc_text(observed_at_utc)
        now_utc = self.clock()
        _utc_text(now_utc)
        age_seconds = (now_utc.astimezone(timezone.utc) - observed_at_utc.astimezone(timezone.utc)).total_seconds()
        if age_seconds < 0 or age_seconds > self.config.stale_after_seconds:
            raise PaperSafetyError("stale quote timestamp")
        values = _normalize_broker_object(lambda: self._quote_values(contract, ticker))
        instrument_id = _instrument_id_from_int(values["con_id"])
        bid_price = _finite_decimal(values["bid_price"], "bid_price")
        ask_price = _finite_decimal(values["ask_price"], "ask_price")
        bid_size = _finite_decimal(values["bid_size"], "bid_size")
        ask_size = _finite_decimal(values["ask_size"], "ask_size")
        if bid_price > ask_price:
            raise PaperSafetyError("crossed quote")
        row = {
            "timestamp_utc": timestamp_utc,
            "instrument_id": instrument_id,
            "bid_price": bid_price,
            "ask_price": ask_price,
            "bid_size": bid_size,
            "ask_size": ask_size,
        }
        self._reject_configured_account([row])
        return self.store.write("paper_quotes", [row])

    def record_order(
        self, decision_id: str, contract: object, order: object, status: str, created_at_utc: datetime
    ) -> int:
        self.validate_session()
        values = _normalize_broker_object(lambda: self._order_values(contract, order))
        row = self._order_row(decision_id, status, created_at_utc, values)
        self._reject_configured_account([row])
        return self.store.write("paper_orders", [row])

    def record_fill(
        self, order_ref: str, contract: object, execution: object, commission_report: object
    ) -> int:
        self.validate_session()
        if commission_report is None:
            raise PaperSafetyError("commission report is required")
        values = _normalize_broker_object(lambda: self._fill_values(contract, execution, commission_report))
        row = self._fill_row(order_ref, values)
        self._reject_configured_account([row])
        return self.store.write("paper_fills", [row])

    def record_positions(self, position_rows: Iterable[object], observed_at_utc: datetime) -> int:
        self.validate_session()
        timestamp_utc = _utc_text(observed_at_utc)
        values = _normalize_broker_object(lambda: self._position_values(position_rows))
        rows = self._position_rows(values, timestamp_utc)
        self._reject_configured_account(rows)
        return self.store.write("paper_positions", rows)

    @staticmethod
    def _quote_values(contract: object, ticker: object) -> dict[str, object]:
        _reject_broker_account(contract)
        _reject_broker_account(ticker)
        return {
            "con_id": _strict_broker_contract_id(contract),
            "bid_price": _broker_decimal(ticker, "bid"),
            "ask_price": _broker_decimal(ticker, "ask"),
            "bid_size": _broker_decimal(ticker, "bidSize"),
            "ask_size": _broker_decimal(ticker, "askSize"),
        }

    def _reject_configured_account(self, rows: Iterable[dict[str, object]]) -> None:
        account_id = self.config.account_id
        if any(account_id in str(value) for row in rows for value in row.values()):
            raise PaperSafetyError("configured account values are not permitted")

    @staticmethod
    def _order_values(contract: object, order: object) -> dict[str, object]:
        _reject_broker_account(contract)
        _reject_broker_account(order)
        return {
            "con_id": _broker_contract_id(contract),
            "order_ref": _broker_text(order, "orderRef"),
            "side": _broker_text(order, "action"),
            "quantity": _broker_integer(order, "totalQuantity"),
            "order_type": _broker_text(order, "orderType"),
            "time_in_force": _broker_text(order, "tif"),
            "ibkr_order_id": _broker_integer(order, "orderId", nullable=True),
        }

    @staticmethod
    def _order_row(
        decision_id: str, status: str, created_at_utc: datetime, values: dict[str, object]
    ) -> dict[str, object]:
        order_ref = _nonempty_text(values["order_ref"], "orderRef")
        normalized_decision_id = _nonempty_text(decision_id, "decision_id")
        normalized_status = _nonempty_text(status, "order status")
        side, quantity = _signed_quantity(values["side"], values["quantity"], "quantity")
        broker_order_id = values["ibkr_order_id"]
        ibkr_order_id = str(broker_order_id) if broker_order_id > 0 else ""
        return {
            "order_ref": order_ref,
            "decision_id": normalized_decision_id,
            "created_at_utc": _utc_text(created_at_utc),
            "instrument_id": _instrument_id_from_text(values["con_id"]),
            "side": side,
            "quantity": quantity,
            "order_type": _nonempty_text(values["order_type"], "order type"),
            "time_in_force": _nonempty_text(values["time_in_force"], "time in force"),
            "status": normalized_status,
            "ibkr_order_id": ibkr_order_id,
        }

    @staticmethod
    def _fill_values(contract: object, execution: object, commission_report: object) -> dict[str, object]:
        _reject_broker_account(contract)
        _reject_broker_account(execution)
        _reject_broker_account(commission_report)
        if commission_report is None:
            raise ValueError("missing commission report")
        return {
            "con_id": _broker_contract_id(contract),
            "fill_id": _broker_text(execution, "execId"),
            "side": _broker_text(execution, "side"),
            "quantity": _broker_integer(execution, "shares"),
            "fill_time_utc": _utc_text(getattr(execution, "time", None)),
            "fill_price": _broker_decimal(execution, "price"),
            "commission_usd": _broker_decimal(commission_report, "commission"),
        }

    @staticmethod
    def _fill_row(order_ref: str, values: dict[str, object]) -> dict[str, object]:
        normalized_side = {"BOT": "BUY", "SLD": "SELL"}.get(values["side"].upper(), values["side"])
        side, quantity = _signed_quantity(normalized_side, values["quantity"], "fill quantity")
        return {
            "fill_id": _nonempty_text(values["fill_id"], "execution ID"),
            "order_ref": _nonempty_text(order_ref, "order_ref"),
            "fill_time_utc": values["fill_time_utc"],
            "instrument_id": _instrument_id_from_text(values["con_id"]),
            "side": side,
            "quantity": quantity,
            "fill_price": _finite_decimal(values["fill_price"], "fill price"),
            "commission_usd": _finite_nonnegative_decimal(values["commission_usd"], "commission"),
        }

    @staticmethod
    def _position_values(position_rows: Iterable[object]) -> list[dict[str, object]]:
        values: list[dict[str, object]] = []
        for position in position_rows:
            _reject_broker_account(position)
            contract = getattr(position, "contract", None)
            _reject_broker_account(contract)
            values.append({
                "con_id": _broker_contract_id(contract),
                "quantity": _broker_integer(position, "position"),
                "average_cost": _broker_decimal(position, "avgCost"),
                "market_price": _broker_decimal(position, "marketPrice"),
                "unrealized_pnl_usd": _broker_decimal(position, "unrealizedPNL"),
                "realized_pnl_usd": _broker_decimal(position, "realizedPNL"),
            })
        return values

    @staticmethod
    def _position_rows(values: list[dict[str, object]], timestamp_utc: str) -> list[dict[str, object]]:
        normalized_rows: list[dict[str, object]] = []
        instrument_ids: set[str] = set()
        for value in values:
            instrument_id = _instrument_id_from_text(value["con_id"])
            if instrument_id in instrument_ids:
                raise PaperSafetyError("duplicate instrument in position snapshot")
            instrument_ids.add(instrument_id)
            integer_quantity = value["quantity"]
            normalized_rows.append({
                "timestamp_utc": timestamp_utc,
                "instrument_id": instrument_id,
                "quantity": integer_quantity,
                "average_cost": _finite_decimal(value["average_cost"], "average cost"),
                "market_price": _finite_decimal(value["market_price"], "market price"),
                "unrealized_pnl_usd": _finite_value(value["unrealized_pnl_usd"], "unrealized P&L"),
                "realized_pnl_usd": _finite_value(value["realized_pnl_usd"], "realized P&L"),
            })
        return normalized_rows
