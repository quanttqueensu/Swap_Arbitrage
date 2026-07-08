from __future__ import annotations

import importlib.util
import os
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

BASE_DIR = PROJECT_CONFIG.BASE_DIR
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
LOG_DIR = AGENT_DIR / "logs"
STATE_DIR = AGENT_DIR / "state"
STATE_FILE = STATE_DIR / "agent_0_state.json"
ORDERS_LOG_FILE = LOG_DIR / "orders.csv"


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

# The current main sizing file stores swap-leg notional, not Eris futures
# contract count. This lets Agent 0 derive a conservative paper quantity cap.
SWAP_NOTIONAL_PER_FUTURE_CONTRACT = 1_000_000

MAX_TRADES_PER_DAY = 5
ALLOW_RANDOM_ENTRIES = True
ALLOW_FLATTENING = True
ALLOW_SIGNAL_BASED_ENTRIES = False


# ============================================================
# Random policy
# ============================================================

RANDOM_SEED_ENV_VAR = "AGENT0_RANDOM_SEED"
SKIP_WEIGHT = 0.35
ENTRY_WEIGHT = 0.45
FLATTEN_WEIGHT = 0.20


# ============================================================
# Order behavior
# ============================================================

DEFAULT_DRY_RUN = False
DEFAULT_ORDER_TYPE = "MKT"
ORDER_TIF = "DAY"
ORDER_REF_PREFIX = AGENT_NAME
LOOP_INTERVAL_SECONDS = 300


def ensure_agent_directories() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


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

    if REQUIRE_PAPER_ACCOUNT_PREFIX and not account_id.upper().startswith(PAPER_ACCOUNT_PREFIX):
        raise RuntimeError(
            f"Agent 0 refuses account {account_id!r}. Expected a paper account "
            f"starting with {PAPER_ACCOUNT_PREFIX!r}."
        )


def settings_summary() -> dict[str, object]:
    return {
        "project_dir": str(BASE_DIR),
        "agent_name": AGENT_NAME,
        "paper_only": PAPER_ONLY,
        "live_trading_enabled": LIVE_TRADING_ENABLED,
        "ibkr_host": IBKR_HOST,
        "ibkr_port": IBKR_PORT,
        "ibkr_client_id": IBKR_CLIENT_ID,
        "min_days_to_expiry": MIN_DAYS_TO_EXPIRY,
        "account_env_var": ACCOUNT_ENV_VAR,
        "required_paper_account_prefix": PAPER_ACCOUNT_PREFIX,
        "allowed_swap_futures": ALLOWED_SWAP_FUTURES,
        "allowed_treasury_futures": ALLOWED_TREASURY_FUTURES,
        "main_sizing_file": str(MAIN_SIZING_FILE),
        "main_size_cap_mode": MAIN_SIZE_CAP_MODE,
        "max_order_size_fraction": MAX_ORDER_SIZE_FRACTION,
        "min_order_qty": MIN_ORDER_QTY,
        "swap_notional_per_future_contract": SWAP_NOTIONAL_PER_FUTURE_CONTRACT,
        "max_trades_per_day": MAX_TRADES_PER_DAY,
        "allow_random_entries": ALLOW_RANDOM_ENTRIES,
        "allow_flattening": ALLOW_FLATTENING,
        "allow_signal_based_entries": ALLOW_SIGNAL_BASED_ENTRIES,
        "skip_weight": SKIP_WEIGHT,
        "entry_weight": ENTRY_WEIGHT,
        "flatten_weight": FLATTEN_WEIGHT,
        "default_dry_run": DEFAULT_DRY_RUN,
        "default_order_type": DEFAULT_ORDER_TYPE,
        "order_tif": ORDER_TIF,
        "loop_interval_seconds": LOOP_INTERVAL_SECONDS,
    }
