from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


InstrumentKind = Literal["swap_future", "treasury_future"]
OrderSide = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class AgentInstrument:
    maturity: str
    symbol: str
    kind: InstrumentKind
    exchange: str = "CBOT"
    currency: str = "USD"

    @property
    def maturity_key(self) -> str:
        return self.maturity.lower()


@dataclass(frozen=True)
class SizingCap:
    instrument: AgentInstrument
    main_quantity: int
    max_agent_quantity: int
    source: str


@dataclass
class QueuedOrder:
    order_ref: str
    activate_at: datetime
    symbol: str
    side: OrderSide
    quantity: int
    status: str = "planned"
    contract_id: str = ""
    order_id: str = ""
