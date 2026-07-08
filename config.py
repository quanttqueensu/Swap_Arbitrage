from pathlib import Path

# ============================================================
# Project paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"

RATES_FILE = DATA_DIR / "treasury_rates.csv"
SWAP_RATES_FILE = DATA_DIR / "swap_rates.csv"
RAW_PRICE_DATA_FILE = DATA_DIR / "raw_price_data.csv"
SIGNAL_DATA_FILE = DATA_DIR / "signal_data.csv"

# Legacy names kept as aliases so older agent/config code reads the consolidated files.
RATES_WITH_SWAPS_FILE = RAW_PRICE_DATA_FILE
SIGNALS_FILE = SIGNAL_DATA_FILE


# ============================================================
# Date range
# ============================================================

START_DATE = "2018-01-01"
END_DATE = None  # None = today


# ============================================================
# Request / cache behavior
# ============================================================

USER_AGENT = "Swap-Arb-Research/1.0"

RETRIES = 6
TIMEOUT = 45
BACKOFF_CAP_SECONDS = 30

TREASURY_PULL_SLEEP_SECONDS = 0.75


# ============================================================
# Model universe
# ============================================================

MATURITIES = ["2Y", "5Y", "10Y", "30Y"]


# ============================================================
# Treasury CMT settings
# ============================================================

TREASURY_XML_BASE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/pages/xml"
)

TREASURY_XML_DATASET = "daily_treasury_yield_curve"

TREASURY_COLUMN_MAP = {
    "NEW_DATE": "date",
    "BC_1YEAR": "dgs1",
    "BC_2YEAR": "dgs2",
    "BC_5YEAR": "dgs5",
    "BC_10YEAR": "dgs10",
    "BC_30YEAR": "dgs30",
}

TREASURY_COLUMNS = {
    "2Y": "dgs2",
    "5Y": "dgs5",
    "10Y": "dgs10",
    "30Y": "dgs30",
}


# ============================================================
# NY Fed funding-rate settings
# ============================================================

NYFED_BASE_URL = "https://markets.newyorkfed.org"

NYFED_RATE_CONFIG = {
    "SOFR": {
        "output_col": "sofr",
        "api_path": "/api/rates/secured/sofr/search.json",
    },
    "EFFR": {
        "output_col": "effr",
        "api_path": "/api/rates/unsecured/effr/search.json",
    },
}

NYFED_RATES = ["SOFR", "EFFR"]


# IBKR-visible Eris SOFR Swap Futures on CBOT
# Current practical universe: 2Y and 5Y only for your target maturities.
ERIS_SOFR_SWAP_FUTURES = {
    "2Y": "YIT",
    "5Y": "YIW",
}

SWAP_COLUMNS = {
    "2Y": "eris_swap_2y_price",
    "5Y": "eris_swap_5y_price",
}

SWAP_RETURN_COLUMNS = {
    "2Y": "eris_swap_2y_return",
    "5Y": "eris_swap_5y_return",
}

IBKR_EXCHANGES_TO_TRY = ["CBOT", "ECBOT"]

IBKR_DURATION = "1 Y"
IBKR_BAR_SIZE = "1 day"
IBKR_WHAT_TO_SHOW = "TRADES"
IBKR_USE_RTH = False


TREASURY_FUTURES = {
    "2Y": "ZT",
    "5Y": "ZF",
    "10Y": "ZN",
    "30Y": "ZB",
}

# ============================================================
# DV01 sizing settings
# ============================================================

SIZED_SIGNALS_FILE = SIGNAL_DATA_FILE

MAX_DV01_PER_MATURITY = {
    "2Y": 3_000,
    "5Y": 3_000,
    "10Y": 2_500,
    "30Y": 1_500,
}

# Approximate DV01 years for $1mm notional.
SWAP_DV01_YEARS = {
    "2Y": 1.9,
    "5Y": 4.6,
    "10Y": 8.5,
    "30Y": 19.0,
}

TREASURY_DV01_YEARS = {
    "2Y": 1.9,
    "5Y": 4.5,
    "10Y": 8.0,
    "30Y": 18.0,
}

# Approximate Treasury futures DV01 per contract.
TREASURY_FUTURES_DV01_PER_CONTRACT = {
    "2Y": 38.0,     # ZT
    "5Y": 58.0,     # ZF
    "10Y": 85.0,    # ZN
    "30Y": 180.0,   # ZB
}

DV01_VOL_LOOKBACK = 63
MIN_DV01_SCALE = 0.25
MAX_DV01_SCALE = 1.00

# ============================================================
# Signal / risk settings
# ============================================================

Z_ENTRY = 2.0
Z_EXIT = 0.5
ROLLING_WINDOW = 252

MAX_GROSS_DV01 = 10_000


# ============================================================
# IBKR connection settings
# ============================================================

IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7497
IBKR_CLIENT_ID = 20
