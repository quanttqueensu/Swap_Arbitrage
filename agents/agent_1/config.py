from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import time
from decimal import Decimal, InvalidOperation
from pathlib import Path


AGENT_NAME = "agent_1"
PAPER_ONLY = True
LIVE_TRADING_ENABLED = False
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7497
POLL_INTERVAL_SECONDS = 30
TIMEZONE = "America/New_York"
PAPER_ACCOUNT_PREFIX = "DU"
AGENT0_CLIENT_ID = 30
CONFIG_ENV_VAR = "AGENT1_PAPER_CONFIG"


class ConfigError(RuntimeError):
    """Raised when Agent 1's immutable paper-only configuration is invalid."""


@dataclass(frozen=True)
class Agent1Config:
    account: str
    client_id: int
    market_open_time: time
    market_close_time: time
    min_days_to_expiry: int
    max_target_age_business_days: int
    max_quote_age_seconds: Decimal
    order_group_timeout_seconds: Decimal
    max_working_order_groups: int
    max_order_groups_per_session: int
    max_gross_dv01: Decimal
    max_net_dv01: Decimal
    max_residual_dv01_fraction: Decimal
    margin_reserve_fraction: Decimal
    max_session_loss_usd: Decimal
    max_drawdown_usd: Decimal
    max_2y_swap_contracts: int
    max_2y_treasury_contracts: int
    max_5y_swap_contracts: int
    max_5y_treasury_contracts: int
    poll_interval_seconds: int = POLL_INTERVAL_SECONDS
    timezone: str = TIMEZONE
    host: str = IBKR_HOST
    port: int = IBKR_PORT


REQUIRED_KEYS = {
    "account",
    "client_id",
    "market_open_time",
    "market_close_time",
    "min_days_to_expiry",
    "max_target_age_business_days",
    "max_quote_age_seconds",
    "order_group_timeout_seconds",
    "max_working_order_groups",
    "max_order_groups_per_session",
    "max_gross_dv01",
    "max_net_dv01",
    "max_residual_dv01_fraction",
    "margin_reserve_fraction",
    "max_session_loss_usd",
    "max_drawdown_usd",
    "max_2y_swap_contracts",
    "max_2y_treasury_contracts",
    "max_5y_swap_contracts",
    "max_5y_treasury_contracts",
}

INTEGER_POSITIVE_KEYS = {
    "client_id",
    "min_days_to_expiry",
    "max_target_age_business_days",
    "max_working_order_groups",
    "max_order_groups_per_session",
    "max_2y_swap_contracts",
    "max_2y_treasury_contracts",
    "max_5y_swap_contracts",
    "max_5y_treasury_contracts",
}

DECIMAL_POSITIVE_KEYS = {
    "max_quote_age_seconds",
    "order_group_timeout_seconds",
    "max_gross_dv01",
    "max_net_dv01",
    "max_session_loss_usd",
    "max_drawdown_usd",
}

FRACTION_KEYS = {
    "max_residual_dv01_fraction",
    "margin_reserve_fraction",
}


def _positive_int(values: dict[str, object], key: str) -> int:
    value = values[key]
    if type(value) is not int or value <= 0:
        raise ConfigError(f"{key} must be a positive integer.")
    return value


def _decimal(values: dict[str, object], key: str, *, fraction: bool = False) -> Decimal:
    value = values[key]
    if isinstance(value, bool):
        raise ConfigError(f"{key} must be a finite positive number.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(f"{key} must be a finite positive number.") from exc

    if not parsed.is_finite() or parsed <= 0:
        raise ConfigError(f"{key} must be a finite positive number.")
    if fraction and parsed > 1:
        raise ConfigError(f"{key} must be in the interval (0, 1].")
    return parsed

def _clock_time(values: dict[str, object], key: str) -> time:
    raw = values[key]
    if type(raw) is not str:
        raise ConfigError(f"{key} must be an HH:MM market time.")
    try:
        parsed = time.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{key} must be an HH:MM market time.") from exc
    if parsed.tzinfo is not None or parsed.second != 0 or parsed.microsecond != 0:
        raise ConfigError(f"{key} must be an HH:MM market time.")
    return parsed


def load_config(path: Path | None = None) -> Agent1Config:
    if not PAPER_ONLY or LIVE_TRADING_ENABLED:
        raise ConfigError("Agent 1 is immutable paper-only software.")
    if IBKR_HOST != "127.0.0.1" or IBKR_PORT != 7497:
        raise ConfigError("Agent 1 requires localhost IBKR paper port 7497.")

    resolved = path
    if resolved is None:
        raw_path = os.getenv(CONFIG_ENV_VAR, "").strip()
        if not raw_path:
            raise ConfigError(
                f"Missing Agent 1 paper configuration path. Set {CONFIG_ENV_VAR}."
            )
        resolved = Path(raw_path)

    try:
        values = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not load Agent 1 paper configuration: {resolved}") from exc

    if not isinstance(values, dict):
        raise ConfigError("Agent 1 paper configuration must be a JSON object.")

    missing = sorted(REQUIRED_KEYS - values.keys())
    if missing:
        raise ConfigError(f"Agent 1 paper configuration is missing: {missing}.")

    account = str(values["account"]).strip()
    if not account.upper().startswith(PAPER_ACCOUNT_PREFIX):
        raise ConfigError("Agent 1 account must be a DU... IBKR paper account.")

    parsed_ints = {key: _positive_int(values, key) for key in INTEGER_POSITIVE_KEYS}
    if parsed_ints["client_id"] == AGENT0_CLIENT_ID:
        raise ConfigError("Agent 1 client_id must be distinct from Agent 0 client ID 30.")

    parsed_decimals = {
        key: _decimal(values, key)
        for key in DECIMAL_POSITIVE_KEYS
    }
    parsed_fractions = {
        key: _decimal(values, key, fraction=True)
        for key in FRACTION_KEYS
    }
    market_open_time = _clock_time(values, "market_open_time")
    market_close_time = _clock_time(values, "market_close_time")
    if market_close_time <= market_open_time:
        raise ConfigError("Agent 1 market_close_time must be after market_open_time.")

    return Agent1Config(
        account=account,
        client_id=parsed_ints["client_id"],
        market_open_time=market_open_time,
        market_close_time=market_close_time,
        min_days_to_expiry=parsed_ints["min_days_to_expiry"],
        max_target_age_business_days=parsed_ints["max_target_age_business_days"],
        max_quote_age_seconds=parsed_decimals["max_quote_age_seconds"],
        order_group_timeout_seconds=parsed_decimals["order_group_timeout_seconds"],
        max_working_order_groups=parsed_ints["max_working_order_groups"],
        max_order_groups_per_session=parsed_ints["max_order_groups_per_session"],
        max_gross_dv01=parsed_decimals["max_gross_dv01"],
        max_net_dv01=parsed_decimals["max_net_dv01"],
        max_residual_dv01_fraction=parsed_fractions["max_residual_dv01_fraction"],
        margin_reserve_fraction=parsed_fractions["margin_reserve_fraction"],
        max_session_loss_usd=parsed_decimals["max_session_loss_usd"],
        max_drawdown_usd=parsed_decimals["max_drawdown_usd"],
        max_2y_swap_contracts=parsed_ints["max_2y_swap_contracts"],
        max_2y_treasury_contracts=parsed_ints["max_2y_treasury_contracts"],
        max_5y_swap_contracts=parsed_ints["max_5y_swap_contracts"],
        max_5y_treasury_contracts=parsed_ints["max_5y_treasury_contracts"],
    )
