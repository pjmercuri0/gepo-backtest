# ── GEPO BACKTEST CONFIGURATION ─────────────────────────────────────────────
# Edit this file to change any backtest parameter.

import os

# ── DATA ─────────────────────────────────────────────────────────────────────
# Folder containing the CSV files from Discount Option Data.
# Daily-cadence copy shares data/output with the canonical Monday backtest
# (one directory up), so we don't have to duplicate ~80MB of parquets.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_REPO_ROOT, "data")

# Output folder for results (shared with root backtest; use -daily suffixes
# in regen_all.py to avoid clobbering canonical Monday output files).
OUTPUT_DIR = os.path.join(_REPO_ROOT, "output")

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
DTE_MIN = 1    # minimum days to expiry (daily cadence matches live_config)
DTE_MAX = 7    # maximum days to expiry

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

# Maximum credit-to-max-loss ratio. Comparison is strict `>` — kept
# iff b ≤ MAX_CREDIT_RATIO, filtered iff b > MAX_CREDIT_RATIO.
# Canonical (2026-05-16+): no cap. High-b trades contribute positive edge
# both in-sample and out-of-sample with no evidence of overfitting.
MAX_CREDIT_RATIO = float("inf")
BACKTEST_MAX_CREDIT_RATIO = float("inf")

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
# Canonical (2026-05-15+): Γᵢ ≥ 0.10% threshold-based selection.
# Γᵢ is dimensionless and reads as percent risk-adjusted EV per trade,
# so a 0.0010 cutoff = 0.10% per-trade hurdle on variance-adjusted EV
# after the entropic ambiguity discount. See paper §7 for the threshold
# sweep and the comparison against the legacy top-N=5 rule.
GROUND_THRESHOLD = 0.0010

# ── CREDIT PRICING BASIS ─────────────────────────────────────────────────────
# Which credit price drives selection AND P&L:
#   "mid"     — short_mid - long_mid (optimistic; backtest classic)
#   "natural" — short_bid - long_ask (worst-case fillable on a combo cross)
#   "midmid"  — midpoint of mid and natural (~realistic fill assumption)
# Per-variant scripts override this in regen_all.py to sweep the three bases.
CREDIT_BASIS = "mid"

# ── SELECTION ────────────────────────────────────────────────────────────────
# Number of top-ranked candidates entered each week. Under the canonical
# Γᵢ ≥ 0.10% threshold rule TOP_N is set to None (no per-week cap); every
# candidate clearing the threshold is taken. Set to a positive integer to
# fall back to the legacy top-N rule.
TOP_N = None

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
