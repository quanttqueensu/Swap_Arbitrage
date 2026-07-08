from __future__ import annotations

from . import config
from .models import AgentPosition, SizingCap, TradeDecision
from .state import AgentState


class RiskLimitError(RuntimeError):
    pass


def validate_decision(
    decision: TradeDecision,
    state: AgentState,
    sizing_caps: dict[str, SizingCap],
    positions: list[AgentPosition],
) -> None:
    if decision.action == "skip":
        return

    if not state.can_submit_trade():
        raise RiskLimitError(
            f"Daily trade cap reached: {state.trades_today}/"
            f"{config.MAX_TRADES_PER_DAY}."
        )

    if decision.instrument is None:
        raise RiskLimitError("Trade decision is missing an instrument.")

    if decision.side not in {"BUY", "SELL"}:
        raise RiskLimitError("Trade decision must have side BUY or SELL.")

    if decision.quantity <= 0:
        raise RiskLimitError("Trade quantity must be positive.")

    if decision.action == "enter":
        _validate_entry(decision, sizing_caps)
        return

    if decision.action == "flatten":
        _validate_flatten(decision, positions)
        return

    raise RiskLimitError(f"Unsupported decision action: {decision.action}")


def _validate_entry(
    decision: TradeDecision,
    sizing_caps: dict[str, SizingCap],
) -> None:
    if not config.ALLOW_RANDOM_ENTRIES:
        raise RiskLimitError("Random entries are disabled.")

    if config.ALLOW_SIGNAL_BASED_ENTRIES:
        raise RiskLimitError("Signal-based entries must stay disabled for Agent 0.")

    assert decision.instrument is not None

    cap = sizing_caps.get(decision.instrument.symbol)

    if cap is None:
        raise RiskLimitError(
            f"No sizing cap found for {decision.instrument.symbol}."
        )

    if cap.max_agent_quantity <= 0:
        raise RiskLimitError(
            f"No positive Agent 0 quantity cap for {decision.instrument.symbol} "
            f"(source={cap.source})."
        )

    if decision.quantity > cap.max_agent_quantity:
        raise RiskLimitError(
            f"Quantity {decision.quantity} exceeds Agent 0 cap "
            f"{cap.max_agent_quantity} for {decision.instrument.symbol}."
        )


def _validate_flatten(
    decision: TradeDecision,
    positions: list[AgentPosition],
) -> None:
    if not config.ALLOW_FLATTENING:
        raise RiskLimitError("Flattening is disabled.")

    assert decision.instrument is not None

    position = next(
        (
            item for item in positions
            if item.instrument.symbol == decision.instrument.symbol
        ),
        None,
    )

    if position is None or position.quantity == 0:
        raise RiskLimitError(
            f"No open position to flatten for {decision.instrument.symbol}."
        )

    if decision.quantity != abs(position.quantity):
        raise RiskLimitError(
            "Flatten order quantity must exactly match the open position size."
        )

    if position.quantity > 0 and decision.side != "SELL":
        raise RiskLimitError("Flattening a long position requires SELL.")

    if position.quantity < 0 and decision.side != "BUY":
        raise RiskLimitError("Flattening a short position requires BUY.")
