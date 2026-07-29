from __future__ import annotations

import importlib.util
import os
from decimal import Decimal
from pathlib import Path


PROJECT_CONFIG_FILE = Path(__file__).resolve().parents[2] / "config.py"
PROJECT_CONFIG_SPEC = importlib.util.spec_from_file_location(
    "swap_arb_project_config",
    PROJECT_CONFIG_FILE,
)

if PROJECT_CONFIG_SPEC is None or PROJECT_CONFIG_SPEC.loader is None:
    raise ImportError(f"Could not load project config from {PROJECT_CONFIG_FILE}")

PROJECT_CONFIG = importlib.util.module_from_spec(PROJECT_CONFIG_SPEC)
PROJECT_CONFIG_SPEC.loader.exec_module(PROJECT_CONFIG)

ERIS_SOFR_SWAP_FUTURES = PROJECT_CONFIG.ERIS_SOFR_SWAP_FUTURES
PROJECT_IBKR_HOST = PROJECT_CONFIG.IBKR_HOST
IBKR_EXCHANGES_TO_TRY = PROJECT_CONFIG.IBKR_EXCHANGES_TO_TRY
SIZED_SIGNALS_FILE = PROJECT_CONFIG.SIZED_SIGNALS_FILE
TREASURY_FUTURES = PROJECT_CONFIG.TREASURY_FUTURES


# ============================================================
# Identity / filesystem
# ============================================================

AGENT_NAME = "agent_0"
AGENT_DIR = Path(__file__).resolve().parent
ORDERS_DIR = AGENT_DIR / "orders"
UPCOMING_ORDERS_FILE = ORDERS_DIR / "upcoming.csv"
PREVIOUS_ORDERS_FILE = ORDERS_DIR / "previous.csv"


# ============================================================
# Paper-only IBKR routing
# ============================================================

PAPER_ONLY = True
LIVE_TRADING_ENABLED = False

IBKR_HOST = PROJECT_IBKR_HOST
IBKR_PORT = 7497
IBKR_CLIENT_ID = 30
IBKR_TIMEOUT_SECONDS = 20
MIN_DAYS_TO_EXPIRY = 14
CONTRACTS_PER_SYMBOL = 3
MAX_WORKING_ORDERS_PER_CONTRACT_SIDE = 15
MARGIN_RESERVE_FRACTION = Decimal("0.10")

# Put the actual paper account ID in this environment variable, not in this file.
ACCOUNT_ENV_VAR = "AGENT0_IBKR_ACCOUNT"
PAPER_ACCOUNT_PREFIX = "DU"
REQUIRE_PAPER_ACCOUNT_PREFIX = True


# ============================================================
# Strategy scope
# ============================================================

ALLOWED_SWAP_FUTURES = dict(ERIS_SOFR_SWAP_FUTURES)
ALLOWED_TREASURY_FUTURES = dict(TREASURY_FUTURES)

MAIN_SIZING_FILE = SIZED_SIGNALS_FILE
MAIN_SIZE_CAP_MODE = "historical_abs_max"
MAX_ORDER_SIZE_FRACTION = 0.10
MIN_ORDER_QTY = 1

# Fallback only: the main sizing file now stores Eris futures contract counts.
SWAP_NOTIONAL_PER_FUTURE_CONTRACT = 250_000

RANDOM_SEED_ENV_VAR = "AGENT0_RANDOM_SEED"
ORDERS_PER_DAY = 5
ACTIVATION_TIMEZONE = "America/New_York"
ACTIVATION_START_HOUR = 9
ACTIVATION_END_HOUR = 15


# ============================================================
# Order behavior
# ============================================================

SUBMISSION_WAIT_SECONDS = 0.25


def get_agent_account_id(account_id: str | None = None) -> str:
    account_id = (account_id or os.getenv(ACCOUNT_ENV_VAR, "")).strip()

    if not account_id:
        raise RuntimeError(
            f"Missing paper account. Set ${ACCOUNT_ENV_VAR} in PowerShell or "
            "pass --account DU... when starting Agent 0."
        )

    return account_id


def assert_paper_only_settings(account_id: str) -> None:
    if not PAPER_ONLY or LIVE_TRADING_ENABLED:
        raise RuntimeError("Agent 0 is configured to be paper-only forever.")

    if IBKR_PORT != 7497:
        raise RuntimeError(
            f"Agent 0 expected IBKR paper port 7497, got {IBKR_PORT}."
        )

    if not REQUIRE_PAPER_ACCOUNT_PREFIX or PAPER_ACCOUNT_PREFIX != "DU":
        raise RuntimeError(
            "Agent 0 requires the immutable DU paper-account policy."
        )

    if not account_id.upper().startswith("DU"):
        raise RuntimeError(
            f"Agent 0 refuses account {account_id!r}. Expected a paper account "
            "starting with 'DU'."
        )
