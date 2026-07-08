from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config


def today_key() -> str:
    return datetime.now().date().isoformat()


@dataclass
class AgentState:
    date: str
    trades_today: int = 0

    @classmethod
    def load(cls, path: Path = config.STATE_FILE) -> "AgentState":
        if not path.exists():
            return cls(date=today_key())

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls(date=today_key())

        loaded = cls(
            date=str(data.get("date", today_key())),
            trades_today=int(data.get("trades_today", 0)),
        )

        loaded.reset_if_new_day()
        return loaded

    def reset_if_new_day(self) -> None:
        current_day = today_key()

        if self.date != current_day:
            self.date = current_day
            self.trades_today = 0

    def save(self, path: Path = config.STATE_FILE) -> None:
        config.ensure_agent_directories()
        path.write_text(
            json.dumps(
                {
                    "date": self.date,
                    "trades_today": self.trades_today,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def can_submit_trade(self) -> bool:
        return self.trades_today < config.MAX_TRADES_PER_DAY

    def record_submitted_trade(self) -> None:
        self.reset_if_new_day()
        self.trades_today += 1


ORDER_LOG_COLUMNS = [
    "timestamp",
    "agent",
    "account",
    "action",
    "symbol",
    "side",
    "quantity",
    "order_type",
    "dry_run",
    "order_id",
    "status",
    "reason",
    "trades_today",
]


def append_order_log(row: dict[str, Any]) -> None:
    config.ensure_agent_directories()
    file_exists = config.ORDERS_LOG_FILE.exists()

    with config.ORDERS_LOG_FILE.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ORDER_LOG_COLUMNS)

        if not file_exists:
            writer.writeheader()

        writer.writerow({column: row.get(column, "") for column in ORDER_LOG_COLUMNS})
