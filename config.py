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

# Minimum open interest on both legs to filter illiquid strikes. Applied
# at runtime in spreads.py to both short and long legs (canonical=100,
# bumped from 10 on 2026-05-12 to suppress stale-mid artifacts that
# the actual-b ranker over-rewards).
MIN_OPEN_INTEREST = 100

# Minimum credit-to-max-loss ratio (filter out under-priced wide-strike spreads).
# A spread with credit/max_loss < this gets rejected at candidate construction.
# 0.30 means: must collect at least 30 cents per $1 of risk.
# Set to 0.0 to disable the filter.
MIN_CREDIT_RATIO = 0.30

# Maximum credit-to-max-loss ratio. Capped at 10.0 (2026-05-12) to
# suppress the small tail of stale-mid artifacts that the OI=100 gate
# alone doesn't catch: trades with b > 10 (collecting >10× their
# max-loss as credit) imply the option market is handing you most of
# the spread width as free credit, which is physically implausible
# even at high IV. The OI=100 filter cleans up the worst, but a
# residual ~12 trades with b ≥ 10 survive and produce non-economic
# G > 1 nat values. Capping b ≤ 10 removes those.
MAX_CREDIT_RATIO = 10.0

# Hard cap on per-share max loss. Spreads where max_loss > $5/share are
# rejected at candidate construction (canonical: $5/share).
MAX_MAX_LOSS = 5.0

# Theta-to-credit ratio floor. Canonical = -inf (filter off).
MIN_THETA_CREDIT_RATIO = float("-inf")

# ── GROUND PARAMETERS ────────────────────────────────────────────────────────
# Canonical scoring uses per-spread payoffs: b = net_credit / max_loss and
# α(b) = (b−1) / (2b) from uniform-in-strikes linear-payoff geometry, so
# Kelly outcomes are {+b, +αb, −1}. The legacy constant α = −0.5 corresponds
# to the b = 1 (binary) special case and is retained only for the b=1
# reference comparisons in sweep_actual_b.py.
ALPHA = -0.5

# Natural log is canonical (2026-05-12). Keeps the framework state-space
# agnostic: same formulas apply to binary, 3-state credit spreads, and
# continuous outcome spaces without re-deriving. DKL upper bound becomes
# ln(n) instead of 1 for n-state outcomes (ln 3 ≈ 1.099 for credit
# spreads); this is purely a unit convention and does not change the
# selection rule.
import math as _math
LOG_BASE = _math.e

# Canonical GROUND (2026-05-13+): rank by Kelly EV · exp(−k·DKL), where
# Kelly EV := exp(G) − 1 is the expected wealth gain per trade at Kelly-
# optimal sizing (variance-adjusted via log-utility) and exp(−k·DKL) is
# the entropic ambiguity discount. Stored GROUND column is the score
# itself (positive number in dimensionless return units), so threshold 0
# is the natural floor — but kept at -inf for rank-only behavior. The
# G > 0 filter inside ground.py drops growth-negative candidates from
# the menu before ranking.
#
# Small-G connection to Hansen-Sargent: Kelly EV = exp(G) − 1 ≈ G for
# small G, so this score is the small-quantity approximation of the HS
# multiplier-preferences functional J_k = G − k·DKL. The two rank ≈
# equivalently in the canonical regime; Kelly EV is preferred for
# display because the resulting score is a directly-readable per-trade
# return after risk adjustment.
GROUND_THRESHOLD = float("-inf")

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
