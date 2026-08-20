#!/usr/bin/env bash
# Parallel on-demand pull: 10 fetchers in parallel, merge, rank.
# Each fetcher writes to its own per-group parquet to avoid HHMM collisions,
# then a Python merge step combines them into the canonical snapshot for
# the ranker to consume.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -f live/cron_env.sh ] && . live/cron_env.sh

TICKERS=(
    "AAPL" "MSFT" "AMZN" "GOOGL" "TSLA" "UNH" "JNJ" "XOM" "JPM" "V"
    "PG" "MA" "NVDA" "HD" "CVX" "MRK" "ABBV" "PEP" "KO" "AVGO"
    "PFE" "COST" "TMO" "WMT" "MCD" "ACN" "ABT" "DHR" "NEE" "LIN"
    "BMY" "ORCL" "TXN" "PM" "UPS" "MS" "RTX" "AMGN" "HON" "QCOM"
    "LOW" "SBUX" "GS" "BAC" "BLK" "MDT" "GILD" "AXP" "ISRG" "VRTX"
    "ADI" "REGN" "SYK" "ZTS" "CB" "BDX" "ADP" "MMC" "SCHW" "TJX"
    "CSX" "SO" "DUK" "ITW" "CME" "CL" "EOG" "USB" "EMR" "MO"
    "FCX" "AON" "PNC" "NSC" "CCI" "WM" "APD" "F" "GM" "GE"
    "BA" "CAT" "DE" "MMM" "IBM" "INTC" "CSCO" "VZ" "T" "DIS"
    "NFLX" "CRM" "NOW" "PYPL"
)

# Eight IB clients (100-107), each running its tickers concurrently via
# asyncio.gather inside fetcher.py. This keeps groups smaller/faster than the
# conservative 6-client setting while staying below the old 10-client burst
# that repeatedly timed out during Gateway's account/execution-sync handshake.
GROUP_SIZE=12
NUM_GROUPS=8

NOW="$(date '+%Y-%m-%d')"
HHMM="$(date '+%H%M')"
DATE_DIR="live/snapshots/$NOW"
mkdir -p "$DATE_DIR"

echo "=== Parallel pull at $(date '+%Y-%m-%d %H:%M:%S') ==="

# Pull Mya-side edits while the option fetchers run. The separate SPY IBKR
# connection runs after all option clients disconnect so it cannot overload
# Gateway during its connection handshake.
(
    if [ -n "${MYA_SSH_HOST:-}" ]; then
        bash live/pull_from_mya.sh 2>&1 | sed "s/^/  [Pull] /"
    fi
) &
PRELUDE_PID=$!

FETCH_BATCH_SIZE="$("${GEPO_PYTHON:-python3}" -c 'from live import live_config; print(live_config.FETCH_BATCH_SIZE)')"
echo "Starting $NUM_GROUPS parallel fetchers (batch size $FETCH_BATCH_SIZE)..."

PIDS=()
GROUP_FILES=()
for g in $(seq 0 $((NUM_GROUPS - 1))); do
    START=$((g * GROUP_SIZE))
    GROUP_TICKERS=("${TICKERS[@]:$START:$GROUP_SIZE}")
    CLIENT_ID=$((100 + g))
    GROUP_OUT="$DATE_DIR/${HHMM}_g${g}.parquet"
    GROUP_FILES+=("$GROUP_OUT")

    (
        # Gateway has repeatedly timed out during bursty client initialization.
        # Stagger eight sessions enough to avoid synchronized account/execution
        # syncs while keeping the run materially faster than the 6-client mode.
        STAGGER="$("${GEPO_PYTHON:-python3}" -c "print($g * 1.5)")"
        sleep "$STAGGER"
        echo "  [G$((g+1))] Fetching ${#GROUP_TICKERS[@]} tickers (clientId=$CLIENT_ID)..."
        "${GEPO_PYTHON:-python3}" -m live.fetcher --tickers "${GROUP_TICKERS[@]}" \
                                --client-id "$CLIENT_ID" \
                                --out "$GROUP_OUT" 2>&1 | sed "s/^/  [G$((g+1))] /"
    ) &
    PIDS+=($!)
done

# Wait for all fetchers
for pid in "${PIDS[@]}"; do
    wait "$pid" || echo "  ✗ fetcher pid $pid exited non-zero (continuing)"
done
echo "✓ All fetchers completed"

# Mya-side actual-credit edits must land before the upload at the end of run.
wait "$PRELUDE_PID" || echo "  ✗ Mya pull exited non-zero (continuing)"

# This uses its own IBKR connection. Run it only after the option sessions
# exit, rather than competing with their Gateway initialization.
#
# HARD WATCHDOG: a wedged SPY connection (Gateway accepts the socket but never
# delivers a quote — seen 2026-07-16 and 2026-07-22) has no internal timeout on
# the blocking connect/reqTickers/reqHistoricalData calls, so it used to hang
# here indefinitely, holding cron_parallel.lock and making every later scan
# SKIP until a manual kill. Run it in the background with a wall-clock kill so a
# stuck fetch can never block the merge/rank/upload steps below. On timeout we
# proceed with the last valid tick (fetch_spy_intraday preserves it on failure).
SPY_TIMEOUT="${SPY_FETCH_TIMEOUT:-90}"
SPY_LOG="$(mktemp -t gepo_spy)"
echo "Refreshing SPY intraday tick (hard timeout ${SPY_TIMEOUT}s)..."
"${GEPO_PYTHON:-python3}" -m live.fetch_spy_intraday > "$SPY_LOG" 2>&1 &
SPY_PID=$!
(
    sleep "$SPY_TIMEOUT"
    if kill -0 "$SPY_PID" 2>/dev/null; then
        echo "watchdog: SPY fetch exceeded ${SPY_TIMEOUT}s — killing pid $SPY_PID" >> "$SPY_LOG"
        kill -TERM "$SPY_PID" 2>/dev/null || true
        sleep 3
        kill -KILL "$SPY_PID" 2>/dev/null || true
    fi
) &
SPY_WATCH_PID=$!
wait "$SPY_PID" 2>/dev/null || echo "  ✗ SPY refresh failed or timed out (continuing with last valid tick)"
# Stop the watchdog early if the fetch already returned, then reap it.
kill -TERM "$SPY_WATCH_PID" 2>/dev/null || true
wait "$SPY_WATCH_PID" 2>/dev/null || true
sed "s/^/  [SPY] /" "$SPY_LOG" 2>/dev/null || true
rm -f "$SPY_LOG"

# Merge all per-group parquets into the canonical HHMM.parquet for the ranker.
FINAL_OUT="$DATE_DIR/${HHMM}.parquet"
echo "Merging group parquets into $FINAL_OUT..."
if ! "${GEPO_PYTHON:-python3}" - <<PYEOF
import sys
from pathlib import Path
import pandas as pd

group_files = [Path(p) for p in [
$(for f in "${GROUP_FILES[@]}"; do echo "    \"$f\","; done)
]]
existing = [p for p in group_files if p.exists()]
if not existing:
    print("  ✗ no per-group parquets exist — fetchers produced no data")
    sys.exit(1)
dfs = [pd.read_parquet(p) for p in existing]
merged = pd.concat(dfs, ignore_index=True)
merged = merged.drop_duplicates(
    subset=["Symbol", "ExpirationDate", "StrikePrice", "PutCall"],
    keep="last",
)
merged.to_parquet("$FINAL_OUT", index=False)
print(f"  ✓ merged {len(existing)} files → {len(merged)} unique rows")
for p in existing:
    p.unlink()
PYEOF
then
    # Pre-open / no-data firing: ranking is impossible, but the SPY tick was
    # refreshed by the prelude. Upload it so the site widget stays current
    # (regression 2026-06-11: removing the standalone SPY upload left Mya
    # serving yesterday's tick until the first post-open scan).
    echo "  no option data (pre-open?) — uploading SPY tick only"
    # Same non-production guard as upload_to_mya.sh. THIS branch is what
    # clobbered Mya on 2026-08-20: a retired host whose fetches all failed
    # rsynced its stale SPY tick over the live one every 30 minutes.
    if [ -f live/NOT_PRODUCTION ] && [ "${GEPO_FORCE_UPLOAD:-0}" != "1" ]; then
        echo "  ⊘ SPY tick upload skipped (live/NOT_PRODUCTION on $(hostname -s))"
    elif [ -n "${MYA_SSH_HOST:-}" ]; then
        rsync -az live/ranked/spy_intraday.json \
            "$MYA_SSH_HOST:/opt/vito/gepo-backtest/live/ranked/spy_intraday.json" \
            && echo "  ✓ SPY tick uploaded to Mya" \
            || echo "  ✗ SPY tick upload failed (continuing)"
    fi
    exit 0
fi

echo "Running ranker..."
"${GEPO_PYTHON:-python3}" -m live.ranker 2>&1 | sed "s/^/  [Ranker] /"
echo "✓ Snapshot, merge, and rankings complete"

# Archive this scan's qualified picks + settle expired ones (Snapshots tab).
"${GEPO_PYTHON:-python3}" -m live.snapshot_picks 2>&1 | sed "s/^/  [Snap] /"

# Freeze during 15:xx. If 15:01 is blank, the 15:31 scan can replace it with
# real picks; if 15:01 has picks, it is kept.
if [ "$(date +%H)" = "15" ]; then
    "${GEPO_PYTHON:-python3}" -m live.freeze_snapshot 2>&1 | sed "s/^/  /"
fi

# Update MTM tracking on all active frozen files using the parquet we
# just merged. Reuses the in-snapshot bid/ask — no extra IBKR fetch.
# Runs AFTER any 15:45 freeze so today's freshly-frozen picks get tracked
# immediately in this same firing.
echo "Running MTM tracker on active frozen files..."
"${GEPO_PYTHON:-python3}" -m live.track_frozen 2>&1 | sed "s/^/  [Tracker] /" || echo "  ✗ tracker failed (non-fatal)"

# Push to Mya if SSH host configured. Soft-fail so a network blip
# doesn't kill the cron exit code.
if [ -n "${MYA_SSH_HOST:-}" ]; then
    echo "Uploading to Mya..."
    bash live/upload_to_mya.sh 2>&1 | sed "s/^/  [Upload] /" || echo "  ✗ upload failed (non-fatal)"
else
    echo "  (skip Mya upload — MYA_SSH_HOST not set)"
fi
