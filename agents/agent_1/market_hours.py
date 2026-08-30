from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def market_is_open(config: object, now: datetime) -> bool:
    if now.utcoffset() is None:
        raise ValueError("Market-hours checks require a timezone-aware time.")
    zone = ZoneInfo(str(getattr(config, "timezone")))
    local = now.astimezone(zone)
    if local.weekday() >= 5:
        return False
    open_time = getattr(config, "market_open_time")
    close_time = getattr(config, "market_close_time")
    return open_time <= local.time().replace(tzinfo=None) < close_time
