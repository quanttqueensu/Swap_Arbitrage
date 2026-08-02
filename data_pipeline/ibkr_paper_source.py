from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from collections.abc import Callable
from typing import Any

from data_pipeline.paper_store import PaperEventStore


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
    if value.tzinfo is None or value.utcoffset() is None:
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
        try:
            connected = self.ib.isConnected()
            accounts = tuple(self.ib.managedAccounts()) if connected else ()
        except Exception:
            raise PaperSafetyError("broker session validation failed") from None
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
