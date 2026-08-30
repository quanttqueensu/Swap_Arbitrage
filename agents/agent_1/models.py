from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal


Maturity = Literal["2Y", "5Y"]
ReconciliationPhase = Literal["hold", "reduce", "expand"]


@dataclass(frozen=True)
class MaturityTarget:
    swap_qty: int
    treasury_qty: int


@dataclass(frozen=True)
class DailyTarget:
    as_of: date
    version: str
    age_business_days: int
    target_2y: MaturityTarget
    target_5y: MaturityTarget

    def for_maturity(self, maturity: Maturity) -> MaturityTarget:
        if maturity == "2Y":
            return self.target_2y
        if maturity == "5Y":
            return self.target_5y
        raise ValueError(f"Unsupported maturity: {maturity!r}")


@dataclass(frozen=True)
class PositionState:
    confirmed_qty: int
    working_qty: int = 0

    @property
    def effective_qty(self) -> int:
        return self.confirmed_qty + self.working_qty


@dataclass(frozen=True)
class MaturityReconciliation:
    swap_delta: int
    treasury_delta: int
    phase: ReconciliationPhase

    @property
    def is_noop(self) -> bool:
        return self.swap_delta == 0 and self.treasury_delta == 0

LegKind = Literal["swap", "treasury"]


@dataclass(frozen=True)
class BoundContract:
    maturity: Maturity
    leg: LegKind
    con_id: int
    symbol: str
    local_symbol: str
    min_tick: Decimal
    risk_id: str
    broker_contract: Any | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class QuoteSnapshot:
    con_id: int
    bid: Decimal
    ask: Decimal
    timestamp: datetime
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None


@dataclass(frozen=True)
class PositionAuditSnapshot:
    quantity: int
    average_cost: Decimal
    market_price: Decimal
    unrealized_pnl_usd: Decimal
    realized_pnl_usd: Decimal


@dataclass(frozen=True)
class WorkingOrderSnapshot:
    order_ref: str
    order_id: int
    con_id: int
    signed_remaining_qty: int
    status: str


@dataclass(frozen=True)
class BrokerSnapshot:
    observed_at: datetime
    positions: dict[int, int]
    working_orders: tuple[WorkingOrderSnapshot, ...]
    quotes: dict[int, QuoteSnapshot]
    position_details: dict[int, PositionAuditSnapshot] = field(default_factory=dict)

    def working_qty(self, con_id: int) -> int:
        return sum(
            order.signed_remaining_qty
            for order in self.working_orders
            if order.con_id == con_id
        )

    def position_state(self, con_id: int) -> PositionState:
        return PositionState(
            confirmed_qty=self.positions.get(con_id, 0),
            working_qty=self.working_qty(con_id),
        )

    def with_quotes(self, quotes: dict[int, object]) -> "BrokerSnapshot":
        return BrokerSnapshot(
            observed_at=self.observed_at,
            positions=dict(self.positions),
            working_orders=self.working_orders,
            quotes=dict(quotes),
            position_details=dict(self.position_details),
        )
