from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum, IntEnum


class PositionState(IntEnum):
    REVERSE = -1
    FLAT = 0
    TRADITIONAL = 1


class TradeDirection(IntEnum):
    REVERSE = -1
    FLAT = 0
    TRADITIONAL = 1


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MKT"
    LIMIT = "LMT"


class TimeInForce(str, Enum):
    DAY = "DAY"


class FlattenUrgency(str, Enum):
    NONE = "none"
    SCHEDULED = "scheduled"
    EMERGENCY = "emergency"


def _text(value: str) -> str:
    if type(value) is not str:
        raise TypeError("text fields must be exact str values")
    if not value.strip():
        raise ValueError("text fields must be nonblank")
    return value


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime:
        raise TypeError("datetime fields must be exact datetime values")
    if value.utcoffset() != timedelta(0):
        raise ValueError("datetime fields must be timezone-aware UTC")
    return value


def _decimal(value: Decimal, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError("decimal fields must be exact Decimal values")
    if not value.is_finite():
        raise ValueError("decimal fields must be finite")
    if positive and value <= 0:
        raise ValueError("decimal fields must be positive")
    if nonnegative and value < 0:
        raise ValueError("decimal fields must be nonnegative")
    return value


def _integer(value: int, *, nonnegative: bool = False) -> int:
    if type(value) is not int:
        raise TypeError("integer fields must be exact int values")
    if nonnegative and value < 0:
        raise ValueError("integer fields must be nonnegative")
    return value


@dataclass(frozen=True, slots=True)
class NamedValue:
    name: str
    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        _text(self.name)
        _decimal(self.value)
        _text(self.unit)


@dataclass(frozen=True, slots=True)
class RateObservation:
    series_id: str
    maturity: str
    rate_bps: Decimal
    source: str
    observed_at_utc: datetime
    available_at_utc: datetime

    def __post_init__(self) -> None:
        _text(self.series_id)
        _text(self.maturity)
        _decimal(self.rate_bps)
        _text(self.source)
        _utc(self.observed_at_utc)
        _utc(self.available_at_utc)
        if self.available_at_utc < self.observed_at_utc:
            raise ValueError("availability cannot precede observation")


@dataclass(frozen=True, slots=True)
class InstrumentObservation:
    instrument_id: str
    price_points: Decimal
    source: str
    observed_at_utc: datetime
    available_at_utc: datetime

    def __post_init__(self) -> None:
        _text(self.instrument_id)
        _decimal(self.price_points, positive=True)
        _text(self.source)
        _utc(self.observed_at_utc)
        _utc(self.available_at_utc)
        if self.available_at_utc < self.observed_at_utc:
            raise ValueError("availability cannot precede observation")


@dataclass(frozen=True, slots=True)
class ContractMetadata:
    instrument_id: str
    maturity: str
    dv01_usd_per_bp: Decimal
    rate_sensitivity_sign: int

    def __post_init__(self) -> None:
        _text(self.instrument_id)
        _text(self.maturity)
        _decimal(self.dv01_usd_per_bp, positive=True)
        _integer(self.rate_sensitivity_sign)
        if self.rate_sensitivity_sign not in (-1, 1):
            raise ValueError("rate sensitivity sign must be -1 or 1")


@dataclass(frozen=True, slots=True)
class PaperPosition:
    instrument_id: str
    quantity_contracts: int

    def __post_init__(self) -> None:
        _text(self.instrument_id)
        _integer(self.quantity_contracts)


@dataclass(frozen=True, slots=True)
class WorkingOrder:
    order_ref: str
    instrument_id: str
    side: OrderSide
    quantity_contracts: int

    def __post_init__(self) -> None:
        _text(self.order_ref)
        _text(self.instrument_id)
        if type(self.side) is not OrderSide:
            raise TypeError("side must be an OrderSide")
        _integer(self.quantity_contracts, nonnegative=True)


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    decision_time_utc: datetime
    rates: tuple[RateObservation, ...]
    instruments: tuple[InstrumentObservation, ...]
    contracts: tuple[ContractMetadata, ...]
    paper_positions: tuple[PaperPosition, ...] = ()
    working_orders: tuple[WorkingOrder, ...] = ()

    def __post_init__(self) -> None:
        _utc(self.decision_time_utc)
        object.__setattr__(self, "rates", tuple(self.rates))
        object.__setattr__(self, "instruments", tuple(self.instruments))
        object.__setattr__(self, "contracts", tuple(self.contracts))
        object.__setattr__(self, "paper_positions", tuple(self.paper_positions))
        object.__setattr__(self, "working_orders", tuple(self.working_orders))
        self._validate_items(self.rates, RateObservation, "rates")
        self._validate_items(self.instruments, InstrumentObservation, "instruments")
        self._validate_items(self.contracts, ContractMetadata, "contracts")
        self._validate_items(self.paper_positions, PaperPosition, "paper_positions")
        self._validate_items(self.working_orders, WorkingOrder, "working_orders")
        for observation in (*self.rates, *self.instruments):
            if observation.available_at_utc > self.decision_time_utc:
                raise ValueError("snapshot observations must be available at decision time")

    @staticmethod
    def _validate_items(items: tuple[object, ...], expected_type: type[object], field_name: str) -> None:
        if any(type(item) is not expected_type for item in items):
            raise TypeError(f"{field_name} must contain {expected_type.__name__} values")
