from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal


AGENT_NAME = "agent_1"
PAPER_ONLY = True
LIVE_TRADING_ENABLED = False
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7497
POLL_INTERVAL_SECONDS = 30
TIMEZONE = "America/New_York"
AGENT0_CLIENT_ID = 30


PAPER_ACCOUNT = ""
CLIENT_ID = 31
MARKET_OPEN_TIME = time(9, 0)
MARKET_CLOSE_TIME = time(15, 0)
MIN_DAYS_TO_EXPIRY = 14
MAX_TARGET_AGE_BUSINESS_DAYS = 2
MAX_QUOTE_AGE_SECONDS = Decimal("10")
ORDER_GROUP_TIMEOUT_SECONDS = Decimal("30")
MAX_WORKING_ORDER_GROUPS = 2
MAX_ORDER_GROUPS_PER_SESSION = 50
MAX_GROSS_DV01 = Decimal("10000")
MAX_NET_DV01 = Decimal("250")
MAX_RESIDUAL_DV01_FRACTION = Decimal("0.05")
MARGIN_RESERVE_FRACTION = Decimal("0.15")
MAX_SESSION_LOSS_USD = Decimal("1000")
MAX_DRAWDOWN_USD = Decimal("1000")
MAX_2Y_SWAP_CONTRACTS = 100
MAX_2Y_TREASURY_CONTRACTS = 100
MAX_5Y_SWAP_CONTRACTS = 100
MAX_5Y_TREASURY_CONTRACTS = 100


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


def load_config() -> Agent1Config:
    if not PAPER_ONLY or LIVE_TRADING_ENABLED:
        raise ConfigError("Agent 1 is immutable paper-only software.")
    if IBKR_HOST != "127.0.0.1" or IBKR_PORT != 7497:
        raise ConfigError("Agent 1 requires localhost IBKR paper port 7497.")

    account = PAPER_ACCOUNT.strip()
    if not account.upper().startswith("DU"):
        raise ConfigError("Set PAPER_ACCOUNT in agents.agent_1.config to a DU paper account.")
    if CLIENT_ID <= 0 or CLIENT_ID == AGENT0_CLIENT_ID:
        raise ConfigError("Agent 1 client_id must be positive and distinct from Agent 0.")
    if MARKET_CLOSE_TIME <= MARKET_OPEN_TIME:
        raise ConfigError("Agent 1 market_close_time must be after market_open_time.")

    return Agent1Config(
        account=account,
        client_id=CLIENT_ID,
        market_open_time=MARKET_OPEN_TIME,
        market_close_time=MARKET_CLOSE_TIME,
        min_days_to_expiry=MIN_DAYS_TO_EXPIRY,
        max_target_age_business_days=MAX_TARGET_AGE_BUSINESS_DAYS,
        max_quote_age_seconds=MAX_QUOTE_AGE_SECONDS,
        order_group_timeout_seconds=ORDER_GROUP_TIMEOUT_SECONDS,
        max_working_order_groups=MAX_WORKING_ORDER_GROUPS,
        max_order_groups_per_session=MAX_ORDER_GROUPS_PER_SESSION,
        max_gross_dv01=MAX_GROSS_DV01,
        max_net_dv01=MAX_NET_DV01,
        max_residual_dv01_fraction=MAX_RESIDUAL_DV01_FRACTION,
        margin_reserve_fraction=MARGIN_RESERVE_FRACTION,
        max_session_loss_usd=MAX_SESSION_LOSS_USD,
        max_drawdown_usd=MAX_DRAWDOWN_USD,
        max_2y_swap_contracts=MAX_2Y_SWAP_CONTRACTS,
        max_2y_treasury_contracts=MAX_2Y_TREASURY_CONTRACTS,
        max_5y_swap_contracts=MAX_5Y_SWAP_CONTRACTS,
        max_5y_treasury_contracts=MAX_5Y_TREASURY_CONTRACTS,
    )
