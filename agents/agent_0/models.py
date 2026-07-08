from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


InstrumentKind = Literal["swap_future", "treasury_future"]
DecisionAction = Literal["skip", "enter", "flatten"]
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


@dataclass(frozen=True)
class AgentPosition:
    instrument: AgentInstrument
    account: str
    quantity: int
    avg_cost: float | None
    contract: Any

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0


@dataclass(frozen=True)
class TradeDecision:
    action: DecisionAction
    reason: str
    instrument: AgentInstrument | None = None
    side: OrderSide | None = None
    quantity: int = 0

    @classmethod
    def skip(cls, reason: str) -> "TradeDecision":
        return cls(action="skip", reason=reason)
