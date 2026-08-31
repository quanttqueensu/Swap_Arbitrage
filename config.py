from pathlib import Path

# ============================================================
# Project paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw_data"
CACHE_DIR = RAW_DATA_DIR / "cache"

RATES_FILE = RAW_DATA_DIR / "treasury_rates.csv"
SWAP_RATES_FILE = RAW_DATA_DIR / "swap_rates.csv"
CME_SWAP_DATA_FILE = RAW_DATA_DIR / "cme_swap_data.csv"
TREASURY_FUTURES_FILE = RAW_DATA_DIR / "treasury_futures.csv"
TREASURY_FUTURES_DATA_FILE = RAW_DATA_DIR / "treasury_futures_data.csv"
RAW_PRICE_DATA_FILE = RAW_DATA_DIR / "raw_price_data.csv"
SIGNAL_DATA_FILE = RAW_DATA_DIR / "signal_data.csv"
RISK_DATA_FILE = RAW_DATA_DIR / "risk_data.csv"


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

MATURITIES = ["2Y", "5Y"]


# ============================================================
# FRED Treasury CMT settings
# ============================================================

FRED_CSV_BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_CMT_SERIES = {"DGS2": "dgs2", "DGS5": "dgs5"}

TREASURY_XML_BASE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/pages/xml"
)
TREASURY_XML_DATASET = "daily_treasury_yield_curve"
TREASURY_COLUMN_MAP = {
    "NEW_DATE": "date",
    "BC_1MONTH": "dgs1mo",
    "BC_2MONTH": "dgs2mo",
    "BC_3MONTH": "dgs3mo",
    "BC_4MONTH": "dgs4mo",
    "BC_6MONTH": "dgs6mo",
    "BC_1YEAR": "dgs1",
    "BC_2YEAR": "dgs2",
    "BC_3YEAR": "dgs3",
    "BC_5YEAR": "dgs5",
    "BC_7YEAR": "dgs7",
    "BC_10YEAR": "dgs10",
    "BC_20YEAR": "dgs20",
    "BC_30YEAR": "dgs30",
}

INTEREST_RATE_COLUMNS = [
    "dgs1mo",
    "dgs2mo",
    "dgs3mo",
    "dgs4mo",
    "dgs6mo",
    "dgs1",
    "dgs2",
    "dgs3",
    "dgs5",
    "dgs7",
    "dgs10",
    "dgs20",
    "dgs30",
    "sofr",
    "effr",
]

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


# ============================================================
# Eris public market data settings
# ============================================================

ERIS_PUBLIC_BASE_URL = "https://files.erisfutures.com/ftp"
ERIS_PUBLIC_START_DATE = "2020-09-04"


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

SWAP_EQUIVALENT_PAR_RATE_COLUMNS = {
    "2Y": "eris_swap_2y_equivalent_par_rate_bps",
    "5Y": "eris_swap_5y_equivalent_par_rate_bps",
}

SWAP_FIXED_COUPON_COLUMNS = {
    "2Y": "eris_swap_2y_fixed_coupon_pct",
    "5Y": "eris_swap_5y_fixed_coupon_pct",
}

SWAP_B_USD_COLUMNS = {
    "2Y": "eris_swap_2y_b_usd",
    "5Y": "eris_swap_5y_b_usd",
}

SWAP_C_USD_COLUMNS = {
    "2Y": "eris_swap_2y_c_usd",
    "5Y": "eris_swap_5y_c_usd",
}

SWAP_PV01_COLUMNS = {
    "2Y": "eris_swap_2y_pv01_usd_per_bp",
    "5Y": "eris_swap_5y_pv01_usd_per_bp",
}

SWAP_EFFECTIVE_DATE_COLUMNS = {
    "2Y": "eris_swap_2y_effective_date",
    "5Y": "eris_swap_5y_effective_date",
}

SWAP_MATURITY_DATE_COLUMNS = {
    "2Y": "eris_swap_2y_maturity_date",
    "5Y": "eris_swap_5y_maturity_date",
}

SWAP_LAST_TRADE_DATE_COLUMNS = {
    "2Y": "eris_swap_2y_last_trade_date",
    "5Y": "eris_swap_5y_last_trade_date",
}

SWAP_TICKER_COLUMNS = {
    "2Y": "eris_swap_2y_ticker",
    "5Y": "eris_swap_5y_ticker",
}

SWAP_RETURN_COLUMNS = {
    "2Y": "eris_swap_2y_return",
    "5Y": "eris_swap_5y_return",
}

SWAP_DV01_COLUMNS = {
    "2Y": "eris_swap_2y_dv01",
    "5Y": "eris_swap_5y_dv01",
}

IBKR_EXCHANGES_TO_TRY = ["CBOT", "ECBOT"]


TREASURY_FUTURES = {
    "2Y": "ZT",
    "5Y": "ZF",
}

# Unattended research source. These are continuous front-month vendor symbols,
# not executable contract-month identifiers.
YAHOO_CHART_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
TREASURY_FUTURES_SOURCE_SYMBOLS = {
    "2Y": "ZT=F",
    "5Y": "ZF=F",
}

TREASURY_FUTURES_PRICE_COLUMNS = {
    "2Y": "treasury_futures_2y_price",
    "5Y": "treasury_futures_5y_price",
}

TREASURY_FUTURES_RETURN_COLUMNS = {
    "2Y": "treasury_futures_2y_return",
    "5Y": "treasury_futures_5y_return",
}

# CME's permanently fixed Eris/Treasury spread ratios: swap contracts per one
# Treasury contract (YIT:ZT = 2:1, YIW:ZF = 1:1).
TREASURY_FUTURES_HEDGE_RATIOS = {
    "2Y": 2.0,
    "5Y": 1.0,
}

# The public master expresses Treasury risk in paired Eris-DV01 units using
# CME's fixed spread ratios. It is a research proxy, not CTD-derived DV01.
TREASURY_FUTURES_DV01_METHOD = "cme_fixed_ics_ratio_proxy"

# Dollar P&L from a one-point futures price move.
TREASURY_FUTURES_DOLLARS_PER_POINT = {
    "2Y": 2_000.0,
    "5Y": 1_000.0,
}

TREASURY_FUTURES_FACE_VALUE = {
    "2Y": 200_000.0,
    "5Y": 100_000.0,
}

ERIS_DOLLARS_PER_POINT = 1_000.0

# ============================================================
# DV01 / risk sizing settings
# ============================================================

SIZED_SIGNALS_FILE = RISK_DATA_FILE

# Main position-size knob: target swap-leg DV01 before vol/signal scaling.
POSITION_SIZE_DV01 = 3_000

POSITION_SIZE_BY_MATURITY = {
    "2Y": POSITION_SIZE_DV01,
    "5Y": POSITION_SIZE_DV01,
}

# Small targets create noisy one-lot churn after rounding.
MIN_TARGET_DV01_TO_TRADE = 100.0

MAX_GROSS_DV01 = 10_000
MAX_NET_DV01 = 250.0

# 0 means no contract-count cap. Use these as live-trading guardrails later.
MAX_SWAP_FUTURES_CONTRACTS = {
    "2Y": 0,
    "5Y": 0,
}

MAX_TREASURY_FUTURES_CONTRACTS = {
    "2Y": 0,
    "5Y": 0,
}

# Approximate DV01 years for $1mm notional conversion and public Eris contract
# selection. Actual master DV01 drives swap contract sizing.
SWAP_DV01_YEARS = {
    "2Y": 1.9,
    "5Y": 4.6,
}

TREASURY_DV01_YEARS = {
    "2Y": 1.9,
    "5Y": 4.5,
}

DV01_VOL_LOOKBACK = 63
MIN_DV01_SCALE = 0.25
MAX_DV01_SCALE = 1.00

# ============================================================
# Signal / risk settings
# ============================================================

# False preserves the existing fixed 2Y/5Y Treasury CMT signal. When True,
# signal_pipeline maturity-matches each Eris equivalent par rate against the
# interpolated Treasury CMT curve.
YIELD_CURVE_CONSTRUCTION_SIGNAL = False

Z_ENTRY = 1.5
Z_EXIT = 0.5
ROLLING_WINDOW = 252


# ============================================================
# IBKR connection settings
# ============================================================

IBKR_HOST = "127.0.0.1"
