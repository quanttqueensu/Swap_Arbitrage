from __future__ import annotations

import os
import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import config
from .models import QueuedOrder, SizingCap


def next_weekdays(today: date) -> list[date]:
    next_monday = today + timedelta(days=7 - today.weekday())
    return [next_monday + timedelta(days=offset) for offset in range(5)]


class RandomPolicy:
    def __init__(self, seed: str | None = None) -> None:
        seed_value = seed if seed is not None else os.getenv(config.RANDOM_SEED_ENV_VAR)
        self.rng = random.Random(seed_value)

    def build_week_plan(
        self,
        sizing_caps: dict[str, SizingCap],
        today: date,
    ) -> list[QueuedOrder]:

        eligible = [
            cap for cap in sizing_caps.values()
            if cap.max_agent_quantity > 0
        ]

        if not eligible:
            raise RuntimeError("No instrument has a positive sizing cap.")

        zone = ZoneInfo(config.ACTIVATION_TIMEZONE)
        plan = []

        for day in next_weekdays(today):
            for sequence in range(1, config.ORDERS_PER_DAY + 1):
                cap = self.rng.choice(eligible)
                seconds = self.rng.randrange(
                    config.ACTIVATION_START_HOUR * 3600,
                    config.ACTIVATION_END_HOUR * 3600 + 1,
                )
                plan.append(
                    QueuedOrder(
                        order_ref=(
                            f"{config.AGENT_NAME}-{day:%Y%m%d}-{sequence:02d}"
                        ),
                        activate_at=(
                            datetime.combine(day, time(), zone)
                            + timedelta(seconds=seconds)
                        ),
                        symbol=cap.instrument.symbol,
                        side=self.rng.choice(["BUY", "SELL"]),
                        quantity=self.rng.randint(1, cap.max_agent_quantity),
                    )
                )

        return plan
