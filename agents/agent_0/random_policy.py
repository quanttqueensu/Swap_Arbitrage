from __future__ import annotations

import os
import random

from . import config
from .models import AgentPosition, SizingCap, TradeDecision


class RandomPolicy:
    def __init__(self) -> None:
        seed_value = os.getenv(config.RANDOM_SEED_ENV_VAR)
        self.rng = random.Random(seed_value)

    def choose(
        self,
        sizing_caps: dict[str, SizingCap],
        positions: list[AgentPosition],
        allow_skip: bool = True,
    ) -> TradeDecision:
        entry_candidates = [
            cap for cap in sizing_caps.values()
            if cap.max_agent_quantity > 0
        ]

        flatten_candidates = [
            position for position in positions
            if position.quantity != 0
        ]

        choices = []
        weights = []

        if allow_skip:
            choices.append("skip")
            weights.append(config.SKIP_WEIGHT)

        if config.ALLOW_RANDOM_ENTRIES and entry_candidates:
            choices.append("enter")
            weights.append(config.ENTRY_WEIGHT)

        if config.ALLOW_FLATTENING and flatten_candidates:
            choices.append("flatten")
            weights.append(config.FLATTEN_WEIGHT)

        if not choices:
            return TradeDecision.skip("no_available_trade_candidates")

        action = self.rng.choices(choices, weights=weights, k=1)[0]

        if action == "skip":
            return TradeDecision.skip("random_skip")

        if action == "enter":
            cap = self.rng.choice(entry_candidates)
            quantity = self.rng.randint(1, cap.max_agent_quantity)
            side = self.rng.choice(["BUY", "SELL"])

            return TradeDecision(
                action="enter",
                reason=f"random_entry:{cap.source}",
                instrument=cap.instrument,
                side=side,
                quantity=quantity,
            )

        position = self.rng.choice(flatten_candidates)
        side = "SELL" if position.quantity > 0 else "BUY"

        return TradeDecision(
            action="flatten",
            reason="random_flatten_allowed_position",
            instrument=position.instrument,
            side=side,
            quantity=abs(position.quantity),
        )
