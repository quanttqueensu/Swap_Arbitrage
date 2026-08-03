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


@dataclass(frozen=True, slots=True)
class SpreadObservation:
    maturity: str
    observation_time_utc: datetime
    fixed_swap_spread_bps: Decimal
    expected_funding_spread_bps: Decimal
    gross_excess_spread_bps: Decimal
    traditional_cost_buffer_bps: Decimal
    reverse_cost_buffer_bps: Decimal
    traditional_net_opportunity_bps: Decimal
    reverse_net_opportunity_bps: Decimal
    z_score: Decimal | None
    observation_count: int
    source_quality_ok: bool
    is_fresh: bool

    def __post_init__(self) -> None:
        _text(self.maturity)
        _utc(self.observation_time_utc)
        _decimal(self.fixed_swap_spread_bps)
        _decimal(self.expected_funding_spread_bps)
        _decimal(self.gross_excess_spread_bps)
        _decimal(self.traditional_cost_buffer_bps, nonnegative=True)
        _decimal(self.reverse_cost_buffer_bps, nonnegative=True)
        _decimal(self.traditional_net_opportunity_bps)
        _decimal(self.reverse_net_opportunity_bps)
        if self.z_score is not None:
            _decimal(self.z_score)
        _integer(self.observation_count, nonnegative=True)
        if type(self.source_quality_ok) is not bool:
            raise TypeError("source_quality_ok must be an exact bool")
        if type(self.is_fresh) is not bool:
            raise TypeError("is_fresh must be an exact bool")


@dataclass(frozen=True, slots=True)
class SignalDecision:
    decision_id: str
    maturity: str
    decision_time_utc: datetime
    prior_state: PositionState
    new_state: PositionState
    direction: TradeDirection
    reason_code: str
    feature_values: tuple[NamedValue, ...]
    strategy_version: str
    configuration_version: str

    def __post_init__(self) -> None:
        _text(self.decision_id)
        _text(self.maturity)
        _utc(self.decision_time_utc)
        if type(self.prior_state) is not PositionState:
            raise TypeError("prior_state must be a PositionState")
        if type(self.new_state) is not PositionState:
            raise TypeError("new_state must be a PositionState")
        if type(self.direction) is not TradeDirection:
            raise TypeError("direction must be a TradeDirection")
        _text(self.reason_code)
        object.__setattr__(self, "feature_values", tuple(self.feature_values))
        if any(type(value) is not NamedValue for value in self.feature_values):
            raise TypeError("feature_values must contain NamedValue values")
        _text(self.strategy_version)
        _text(self.configuration_version)


@dataclass(frozen=True, slots=True)
class TargetPosition:
    maturity: str
    swap_instrument_id: str
    treasury_instrument_id: str
    swap_quantity_contracts: int
    treasury_quantity_contracts: int
    target_dv01_usd_per_bp: Decimal
    gross_dv01_usd_per_bp: Decimal
    residual_net_dv01_usd_per_bp: Decimal
    expected_turnover_contracts: int
    expected_cost_usd: Decimal
    rounding_diagnostic: str
    cap_diagnostic: str

    def __post_init__(self) -> None:
        _text(self.maturity)
        _text(self.swap_instrument_id)
        _text(self.treasury_instrument_id)
        _integer(self.swap_quantity_contracts)
        _integer(self.treasury_quantity_contracts)
        _decimal(self.target_dv01_usd_per_bp, nonnegative=True)
        _decimal(self.gross_dv01_usd_per_bp, nonnegative=True)
        _decimal(self.residual_net_dv01_usd_per_bp)
        _integer(self.expected_turnover_contracts, nonnegative=True)
        _decimal(self.expected_cost_usd, nonnegative=True)
        _text(self.rounding_diagnostic)
        _text(self.cap_diagnostic)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    scale: Decimal
    reason_codes: tuple[str, ...]
    flatten_requested: bool
    urgency: FlattenUrgency
    limits: tuple[NamedValue, ...]
    measured_values: tuple[NamedValue, ...]

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise TypeError("allowed must be an exact bool")
        _decimal(self.scale)
        if not Decimal("0") <= self.scale <= Decimal("1"):
            raise ValueError("scale must be between 0 and 1")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        for reason_code in self.reason_codes:
            _text(reason_code)
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason_codes must be unique")
        if type(self.flatten_requested) is not bool:
            raise TypeError("flatten_requested must be an exact bool")
        if type(self.urgency) is not FlattenUrgency:
            raise TypeError("urgency must be a FlattenUrgency")
        object.__setattr__(self, "limits", tuple(self.limits))
        object.__setattr__(self, "measured_values", tuple(self.measured_values))
        if any(type(value) is not NamedValue for value in self.limits):
            raise TypeError("limits must contain NamedValue values")
        if any(type(value) is not NamedValue for value in self.measured_values):
            raise TypeError("measured_values must contain NamedValue values")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    run_id: str
    agent_id: str
    strategy_id: str
    decision_id: str
    instrument_id: str
    side: OrderSide
    quantity_contracts: int
    order_type: OrderType
    time_in_force: TimeInForce
    earliest_submission_utc: datetime
    activate_at_utc: datetime
    expires_at_utc: datetime
    reference_price_points: Decimal
    max_slippage_price_points: Decimal
    paper_only: bool

    def __post_init__(self) -> None:
        _text(self.run_id)
        _text(self.agent_id)
        _text(self.strategy_id)
        _text(self.decision_id)
        _text(self.instrument_id)
        if type(self.side) is not OrderSide:
            raise TypeError("side must be an OrderSide")
        _integer(self.quantity_contracts, nonnegative=True)
        if type(self.order_type) is not OrderType:
            raise TypeError("order_type must be an OrderType")
        if type(self.time_in_force) is not TimeInForce:
            raise TypeError("time_in_force must be a TimeInForce")
        _utc(self.earliest_submission_utc)
        _utc(self.activate_at_utc)
        _utc(self.expires_at_utc)
        if not self.earliest_submission_utc <= self.activate_at_utc <= self.expires_at_utc:
            raise ValueError("intent timestamps must be ordered")
        _decimal(self.reference_price_points, positive=True)
        _decimal(self.max_slippage_price_points, nonnegative=True)
        if self.paper_only is not True:
            raise ValueError("paper_only must be True")
