# ── GEPO BACKTEST CONFIGURATION ─────────────────────────────────────────────
# Edit this file to change any backtest parameter.

import os

# ── DATA ─────────────────────────────────────────────────────────────────────
# Folder containing the CSV files from Discount Option Data
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# Output folder for results
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# ── DATE RANGE ───────────────────────────────────────────────────────────────
# Canonical extended-sample range. Per-variant scripts (regen_all, run_qtyx,
# sweep_k, run_paper_runs) override these for their specific window.
START_DATE = "2020-01-01"
END_DATE   = "2026-05-04"

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

# Minimum credit-to-max-loss ratio (filter out under-priced wide-strike spreads).
# A spread with credit/max_loss < this gets rejected at candidate construction.
# 0.30 means: must collect at least 30 cents per $1 of risk.
# Set to 0.0 to disable the filter.
MIN_CREDIT_RATIO = 0.30

# Maximum credit-to-max-loss ratio (filter out structurally weird spreads).
# Above ~1.0 typically means deep-ITM short legs, stale/illiquid quotes,
# or tiny spread widths where max_loss approaches zero. The math explodes:
# credit_ratio = net_credit / (spread_width - net_credit), so a $2.45 credit
# on a $2.50 spread yields ratio 49. Disabled by default; set via
# --max-credit-ratio CLI flag.
MAX_CREDIT_RATIO = float("inf")

# Hard cap on per-share max loss. Spreads where max_loss > $5/share are
# rejected at candidate construction (canonical: $5/share).
MAX_MAX_LOSS = 5.0

# Theta-to-credit ratio floor. Canonical = -inf (filter off).
MIN_THETA_CREDIT_RATIO = float("-inf")

# ── GROUND PARAMETERS ────────────────────────────────────────────────────────
# From Mercurio et al. (2020) eq 28: alpha = mean partial return
ALPHA = -0.5

# Base 3 is canonical: a credit spread has 3 states (p, q, r), so
# uniform entropy = log_3(3) = 1 and DKL from uniform lives in [0, 1].
# GROUND uses 3 ** (k · DKL) in the denominator to keep units consistent.
LOG_BASE = 3

# Minimum GROUND score to enter a trade (below this = PASS).
# Canonical = 0.0 (every selected trade is taken; ranking does the work).
GROUND_THRESHOLD = 0.0

# Loss probability factor: q = delta * LOSS_FACTOR
# Splits the ITM delta probability into full loss vs partial
LOSS_FACTOR = 0.60

# ── SELECTION ────────────────────────────────────────────────────────────────
# Number of top-ranked candidates entered each week (canonical = 5).
TOP_N = 5

# Sizing rule: "1" = 1 contract per spread, "2" = 2 contracts, "dyn10k" = dynamic.
# Per-variant scripts override this.
SIZING = "1"

# Probability-estimation lookback window in days (canonical = 0, i.e. no
# realised-vol blend; pure short-leg implied vol).
LOOKBACK = 0

# Implied-vol drift adjustment. Canonical = off.
USE_DRIFT    = False
DRIFT_WINDOW = 60

# ── REGIME FILTER ────────────────────────────────────────────────────────────
# SPY 100-day SMA gate (canonical). REGIME_FILTER=True → bull-puts allowed
# above the SMA, bear-calls allowed below. Single global series, not per-ticker.
REGIME_FILTER = True
REGIME_WINDOW = 100
REGIME_SOURCE = "spy"

# ── BANKROLL ─────────────────────────────────────────────────────────────────
STARTING_BANKROLL = 10_000.0

# Per-trade contract cap for half-Kelly sizing. Prevents tiny-max-loss
# spreads (e.g. $0.05 max_loss → notional 1000+ contracts) from being
# absurdly oversized. Set higher if your bankroll is large.
MAX_CONTRACTS = 50

# ── ENTRY DAY ────────────────────────────────────────────────────────────────
# Which day of week to enter trades (0=Monday, 1=Tuesday, ... 4=Friday)
ENTRY_DOW = 0   # Monday

# ── OUTPUT ───────────────────────────────────────────────────────────────────
RESULTS_CSV      = "results.csv"
EQUITY_CURVE_PNG = "equity_curve.png"
TRADES_CSV       = "all_trades.csv"
