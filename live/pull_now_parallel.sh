#!/usr/bin/env bash
# Parallel on-demand pull: 10 fetchers in parallel, merge, rank.
# Each fetcher writes to its own per-group parquet to avoid HHMM collisions,
# then a Python merge step combines them into the canonical snapshot for
# the ranker to consume.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Avoid xcrun failures from a stale inherited developer-tools path.
unset DEVELOPER_DIR

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

# 10 IB clients (100-109), each running its 10 tickers concurrently via
# asyncio.gather inside fetcher.py. Per-fetcher wall clock = slowest single
# ticker (~20s), so overall wall clock ~20-25s. Reverted from 20/5 on
# 2026-05-27 after per-ticker async cut intra-fetcher serial cost.
GROUP_SIZE=10
NUM_GROUPS=10

NOW="$(date '+%Y-%m-%d')"
HHMM="$(date '+%H%M')"
DATE_DIR="live/snapshots/$NOW"
mkdir -p "$DATE_DIR"

echo "=== Parallel pull at $(date '+%Y-%m-%d %H:%M:%S') ==="

# Prelude runs concurrently with the option fetchers (saves ~25s of serial
# time). Both halves only need to finish before the RANKER: pull_from_mya
# protects Mya-side actual_credit edits from the end-of-run upload, and the
# SPY refresh feeds the ranker's regime/vol-gate (clientId 12; no collision
# with fetcher clientIds 100+).
(
    if [ -n "${MYA_SSH_HOST:-}" ]; then
        bash live/pull_from_mya.sh 2>&1 | sed "s/^/  [Pull] /"
    fi
    python3 -m live.fetch_spy_intraday 2>&1 | sed "s/^/  [SPY] /"
) &
PRELUDE_PID=$!

echo "Starting $NUM_GROUPS parallel fetchers (batch size $(python3 -c 'from live import live_config; print(live_config.FETCH_BATCH_SIZE)'))..."

PIDS=()
GROUP_FILES=()
for g in $(seq 0 $((NUM_GROUPS - 1))); do
    START=$((g * GROUP_SIZE))
    GROUP_TICKERS=("${TICKERS[@]:$START:$GROUP_SIZE}")
    CLIENT_ID=$((100 + g))
    GROUP_OUT="$DATE_DIR/${HHMM}_g${g}.parquet"
    GROUP_FILES+=("$GROUP_OUT")

    (
        # Stagger handshakes 0.5s apart: 20 simultaneous connects can time out
        # when the Gateway (or this Mac) is loaded (14:31 2026-06-10: 14/20
        # groups failed handshake under both-rights load + local CPU pressure).
        sleep "$(echo "$g * 0.5" | bc)"
        echo "  [G$((g+1))] Fetching ${#GROUP_TICKERS[@]} tickers (clientId=$CLIENT_ID)..."
        python3 -m live.fetcher --tickers "${GROUP_TICKERS[@]}" \
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

# Prelude (Mya pull + SPY refresh) must land before the merge decision so a
# pre-open firing (fetchers empty) can still push the fresh SPY tick to Mya.
wait "$PRELUDE_PID" || echo "  ✗ prelude (Mya pull / SPY refresh) exited non-zero (continuing)"

# Merge all per-group parquets into the canonical HHMM.parquet for the ranker.
FINAL_OUT="$DATE_DIR/${HHMM}.parquet"
echo "Merging group parquets into $FINAL_OUT..."
if ! python3 - <<PYEOF
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
    if [ -n "${MYA_SSH_HOST:-}" ]; then
        rsync -az live/ranked/spy_intraday.json \
            "$MYA_SSH_HOST:/opt/vito/gepo-backtest/live/ranked/spy_intraday.json" \
            && echo "  ✓ SPY tick uploaded to Mya" \
            || echo "  ✗ SPY tick upload failed (continuing)"
    fi
    exit 0
fi

echo "Running ranker..."
python3 -m live.ranker 2>&1 | sed "s/^/  [Ranker] /"
echo "✓ Snapshot, merge, and rankings complete"

# Archive this scan's qualified picks + settle expired ones (Snapshots tab).
python3 -m live.snapshot_picks 2>&1 | sed "s/^/  [Snap] /"

# Freeze the 15:01 basket, then top up vacant slots from the 15:31 ranking.
# freeze_snapshot preserves original picks/tracking/fills, adds only unique
# spreads, and records the established freeze_added_at/freeze_topup_* metadata.
if [ "$(date +%H)" = "15" ]; then
    python3 -m live.freeze_snapshot
fi

# Update MTM tracking on all active frozen files using the parquet we
# just merged. Reuses the in-snapshot bid/ask — no extra IBKR fetch.
# Runs AFTER any 15:45 freeze so today's freshly-frozen picks get tracked
# immediately in this same firing.
echo "Running MTM tracker on active frozen files..."
python3 -m live.track_frozen 2>&1 | sed "s/^/  [Tracker] /" || echo "  ✗ tracker failed (non-fatal)"

# Push to Mya if SSH host configured. Soft-fail so a network blip
# doesn't kill the cron exit code.
if [ -n "${MYA_SSH_HOST:-}" ]; then
    echo "Uploading to Mya..."
    bash live/upload_to_mya.sh 2>&1 | sed "s/^/  [Upload] /" || echo "  ✗ upload failed (non-fatal)"
else
    echo "  (skip Mya upload — MYA_SSH_HOST not set)"
fi
