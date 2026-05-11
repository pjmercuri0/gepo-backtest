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

# --- IBKR gateway ---
IB_HOST       = "127.0.0.1"
IB_PORT       = 4002             # paper 4002, live 4001
IB_CLIENT_ID  = 11               # avoid collision with test_ib_chain.py (=1)
IB_MKT_DATA_TYPE = 3             # 3 = delayed; 4 = delayed-frozen. Use 3 for live polling.

# --- Fetch behaviour ---
FETCH_BATCH_SIZE        = 50      # contracts per reqTickers call
FETCH_PER_TICKER_TIMEOUT = 25     # seconds before giving up on a ticker
FETCH_RETRY_ON_NO_GREEKS = True   # one retry if Greeks come back None

# --- Schedule (US/Eastern wall-clock) ---
# Fetcher runs every 15 min during this window.
FETCH_WINDOW_START = "09:30"
FETCH_WINDOW_END   = "16:30"

# Freeze at 15:45 wall-clock → captures 15:30 market-data prices (the actual
# entry-decision view given the 15-min delay).
FREEZE_AT = "15:45"

# Drift snapshot at 16:15 wall-clock → captures 16:00 close prices for
# decision-vs-close comparison.
DRIFT_AT = "16:15"

# --- Webapp ---
WEBAPP_HOST = "127.0.0.1"
WEBAPP_PORT = 5050
WEBAPP_POLL_SECONDS = 900         # browser polls /api/latest.json this often
                                  # (15 min — matches the fetcher cadence; no
                                  # point polling more often than new data arrives)
TOP_N_DISPLAY = 5                 # canonical top-N picks
TICKER_LIMIT = 30                 # max ranked candidates shown in the scrolling ticker
