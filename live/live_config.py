"""Configuration specific to the live tracker.

Inherits canonical strategy knobs from the project's top-level `config.py`
(do not duplicate them here). Only fields specific to live operation belong in
this module.
"""
import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

SNAPSHOTS_DIR     = os.path.join(ROOT_DIR, "snapshots")
RANKED_DIR        = os.path.join(ROOT_DIR, "ranked")
FROZEN_DIR        = os.path.join(ROOT_DIR, "frozen")
DRIFT_DIR         = os.path.join(ROOT_DIR, "drift")
NOTIFICATIONS_DIR = os.path.join(ROOT_DIR, "notifications")
LOGS_DIR          = os.path.join(ROOT_DIR, "logs")
CACHE_DIR         = os.path.join(ROOT_DIR, "cache")

# --- IBKR gateway ---
IB_HOST       = "127.0.0.1"
IB_PORT       = int(os.environ.get("IB_PORT", 4001))  # paper 4002, live 4001 (env-overridable)
IB_CLIENT_ID  = 11               # avoid collision with test_ib_chain.py (=1)
IB_CONNECT_TIMEOUT = 30          # seconds per ib.connect() attempt. ib_insync defaults to 4,
                                 # which is far too tight: pull_now_parallel launches 10 fetchers
                                 # at once and the Gateway serialises the API handshake, so the
                                 # last groups in the queue always timed out (G8/G9/G10 only,
                                 # 75 occurrences in parallel_pull.log; G1-G7 never).
IB_CONNECT_ATTEMPTS = 3          # retries around that timeout, backing off 2s then 4s.

IB_MKT_DATA_TYPE = 1             # 1=live, 2=frozen, 3=delayed, 4=delayed-frozen. Account has
                                 # Snapshot Bundle + OPRA Streaming Add-On subscribed, so type 1
                                 # serves real-time. Was 3 before 2026-05-26.

# --- Fetch behaviour ---
FETCH_BATCH_SIZE        = 100     # contracts per reqTickers call (bumped from 50)
FETCH_PER_TICKER_TIMEOUT = 150    # Wall-clock budget for a fetcher group. All tickers in a
                                  # group run concurrently under one shared deadline, so this
                                  # bounds the group, not each ticker. Production groups take
                                  # 48-95s (2026-08-31 logs); 25 killed every ticker.
FETCH_RETRY_ON_NO_GREEKS = True   # one retry if Greeks come back None
# Gateway can delay its initial account/execution sync when several clients
# connect together. Retry the connection itself before dropping a whole group.
IB_CONNECT_TIMEOUT      = 15
IB_CONNECT_ATTEMPTS     = 3
IB_CONNECT_RETRY_DELAY  = 3

# --- Live DTE window ---
# Weekly expiry window for live scans. Mon→DTE 4, Tue→DTE 3, Wed→DTE 2,
# Thu→DTE 1, Fri→DTE 0 for same-week Friday expiry. The upper bound gives
# holiday-shifted / calendar edge cases enough room without targeting the
# following week's normal Friday.
LIVE_DTE_MIN = 0
LIVE_DTE_MAX = 6


def live_dte_window(day=None):
    """Return the active live DTE window for the given calendar day."""
    return LIVE_DTE_MIN, LIVE_DTE_MAX

# --- Live OI gate ---
# Canonical backtest uses MIN_OPEN_INTEREST = 100, but live IBKR feeds
# often return NaN for OI mid-session (today's OI is end-of-day published).
# Set to 0 for live so the ranker doesn't drop every row before it can
# rank. Backtest config stays at 100 — preprocessed CSVs already filtered
# on OI so it doesn't bite there.
LIVE_MIN_OPEN_INTEREST = 0

# --- Live credit basis (2026-06-10) ---
# Backtest canon scores on BBO-clamped LAST (EOD vendor data, synchronous).
# Live intraday LAST prints are asynchronous across legs: pairing a stale
# short-LAST-near-ask with a stale long-LAST-near-bid books phantom credits
# inside wide MM quotes (audit 2026-06-10: guaranteed-fill credits negative
# on 4/6 top picks while displayed Kelly EV showed +40-70%). Mid quotes are
# simultaneous; real fills (2026-05-28 only, n=5) ran ~0.82 x mid.
LIVE_CREDIT_BASIS = "mid"

# Selection scores on RAW mid (depth analysis 2026-06-10, 870k same-day
# prints: trades center on mid at every quote width — mid IS the market).
# The fill haircut (real fills ran ~0.82 x mid, n=5) is a realization-side
# stress parameter in the backtest, not a selection input.
LIVE_CREDIT_SCALE = 1.0

# --- Vol gate ---
# DISABLED 2026-05-30 after re-test on LAST credit / Mon+Thu canon:
# the 196 high-vol Thursdays the gate dropped 2022-2025 were +$3,438
# (mean +$17.54, strong contributors). Gating cost ~27% of profit and
# 0.18 Sharpe. Original rationale (Sept-Oct 2022 bear_call losses) was
# under MID credit / all-4-days / different threshold — doesn't apply
# under canonical config. Set back to 20.0 to re-enable.
RV_GATE_THRESHOLD = None

# --- Schedule (US/Eastern wall-clock) ---
# Fetcher runs every 15 min during this window.
FETCH_WINDOW_START = "09:30"
FETCH_WINDOW_END   = "16:30"

# Freeze at 15:01 — moved earlier 2026-05-27 because 15:45 left only ~15 min
# to place limit orders before close, which proved insufficient for fills on
# illiquid weekly spreads. 15:01 gives ~57 min execution window.
FREEZE_AT = "15:01"

# DRIFT_AT was used by cron_drift.sh, which has been removed (2026-05-26).
# With live data, signal time = market time, so the drift's second-pass
# overwrite no longer serves a purpose. Constant kept for backward
# compatibility with any historical references; ignore for new code.
DRIFT_AT = None

# --- Ex-dividend gate scope ---
# Bear calls are ALWAYS gated on ex-div (canonical 2026-06-09): a short call can
# be early-assigned the day before ex-div so the holder captures the dividend,
# hence the +1 day buffer past expiry on that side.
#
# Bull puts carry no dividend-driven early-exercise incentive. The only exposure
# is the ex-div price drop (~the dividend amount) moving against a bullish
# position, and that drop is known in advance and already priced into the
# premium — so gating puts may drop trades whose risk you were already paid for.
# Left OFF pending measurement; the ranker logs what WOULD have been dropped so
# the decision can be made on evidence.
LIVE_EXDIV_GATE_PUTS = False

# --- Webapp ---
WEBAPP_HOST = "127.0.0.1"
WEBAPP_PORT = 5050
WEBAPP_POLL_SECONDS = 900         # browser polls /api/latest.json this often
                                  # (15 min — matches the fetcher cadence; no
                                  # point polling more often than new data arrives)
TOP_N_DISPLAY = 5                 # canonical top-N picks
