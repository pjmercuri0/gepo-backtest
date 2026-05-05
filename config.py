# ── GEPO BACKTEST CONFIGURATION ─────────────────────────────────────────────
# Edit this file to change any backtest parameter.

import os

# ── DATA ─────────────────────────────────────────────────────────────────────
# Folder containing the CSV files from Discount Option Data
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# Output folder for results
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# ── DATE RANGE ───────────────────────────────────────────────────────────────
START_DATE = "2021-01-01"
END_DATE   = "2025-12-31"

# ── TICKERS ──────────────────────────────────────────────────────────────────
# S&P100 subset — remove any you don't want
SP100_TICKERS = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "TSLA", "UNH",  "JNJ",  "XOM",
    "JPM",  "V",    "PG",   "MA",    "NVDA", "HD",   "CVX",  "MRK",
    "ABBV", "PEP",  "KO",   "AVGO",  "PFE",  "COST", "TMO",  "WMT",
    "MCD",  "ACN",  "ABT",  "DHR",   "NEE",  "LIN",  "BMY",  "ORCL",
    "TXN",  "PM",   "UPS",  "MS",    "RTX",  "AMGN", "HON",  "QCOM",
    "LOW",  "SBUX", "GS",   "BAC",   "BLK",  "MDT",  "GILD", "AXP",
    "ISRG", "VRTX", "ADI",  "REGN",  "SYK",  "ZTS",  "CB",   "BDX",
    "ADP",  "MMC",  "SCHW", "TJX",   "CSX",  "SO",   "DUK",  "ITW",
    "CME",  "CL",   "EOG",  "USB",   "EMR",  "MO",   "FCX",  "AON",
    "PNC",  "NSC",  "CCI",  "WM",    "APD",  "F",    "GM",   "GE",
    "BA",   "CAT",  "DE",   "MMM",   "IBM",  "INTC", "CSCO", "VZ",
    "T",    "DIS",  "NFLX", "CRM",   "NOW",  "PYPL",
]

# ── SPREAD PARAMETERS ─────────────────────────────────────────────────────────
# Target delta for short leg (paper uses closest to but not exceeding 0.50)
DELTA_TARGET   = 0.50
DELTA_MIN      = 0.35   # eligible range lower bound
DELTA_MAX      = 0.65   # eligible range upper bound

# Days to expiry: target nearest weekly expiry (4-6 days from entry)
DTE_MIN = 3    # minimum days to expiry
DTE_MAX = 8    # maximum days to expiry

# Minimum open interest on short leg to filter illiquid strikes
MIN_OPEN_INTEREST = 10

# ── GROUND PARAMETERS ────────────────────────────────────────────────────────
# From Mercurio et al. (2020) eq 28: alpha = mean partial return
ALPHA = -0.5

# Log base (3 states: win, partial, loss)
LOG_BASE = 3

# Minimum GROUND score to enter a trade (below this = PASS)
GROUND_THRESHOLD = 0.10

# Loss probability factor: q = delta * LOSS_FACTOR
# Splits the ITM delta probability into full loss vs partial
LOSS_FACTOR = 0.60

# ── BANKROLL ─────────────────────────────────────────────────────────────────
STARTING_BANKROLL = 10_000.0

# ── ENTRY DAY ────────────────────────────────────────────────────────────────
# Which day of week to enter trades (0=Monday, 1=Tuesday, ... 4=Friday)
ENTRY_DOW = 0   # Monday

# ── OUTPUT ───────────────────────────────────────────────────────────────────
RESULTS_CSV      = "results.csv"
EQUITY_CURVE_PNG = "equity_curve.png"
TRADES_CSV       = "all_trades.csv"
