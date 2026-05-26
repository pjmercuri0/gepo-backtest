#!/usr/bin/env bash
# Parallel on-demand pull: 10 fetchers in parallel, merge, rank.
# Each fetcher writes to its own per-group parquet to avoid HHMM collisions,
# then a Python merge step combines them into the canonical snapshot for
# the ranker to consume.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

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

# Doubled from 10/10 → 5/20 on 2026-05-26 to halve total fetch wall-clock.
# 20 IB clients (100-119) is well below the 32-client default limit.
# If parallel_pull.log starts showing "Error 100: Max rate of messages"
# revert to GROUP_SIZE=10, NUM_GROUPS=10.
GROUP_SIZE=5
NUM_GROUPS=20

NOW="$(date '+%Y-%m-%d')"
HHMM="$(date '+%H%M')"
DATE_DIR="live/snapshots/$NOW"
mkdir -p "$DATE_DIR"

echo "=== Parallel pull at $(date '+%Y-%m-%d %H:%M:%S') ==="
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

# Merge all per-group parquets into the canonical HHMM.parquet for the ranker.
FINAL_OUT="$DATE_DIR/${HHMM}.parquet"
echo "Merging group parquets into $FINAL_OUT..."
python3 - <<PYEOF
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

echo "Running ranker..."
python3 -m live.ranker 2>&1 | sed "s/^/  [Ranker] /"
echo "✓ Snapshot, merge, and rankings complete"

# On the dedicated 15:45 firing, freeze BEFORE the tracker so the
# newly-frozen file gets its first mark captured in this same firing
# (otherwise it'd wait until tomorrow and miss the rest of today).
#
# Gate: hour=15 AND minute>=45 AND today's frozen file doesn't yet exist.
# - minute>=45 stops the 15:31 hourly pull from triggering the freeze
#   early (only the 15:45 special firing should freeze).
# - File-exists guard makes the step idempotent; manual reruns won't
#   clobber an existing freeze. To force a re-freeze, delete
#   live/frozen/$(date +%F).json first.
TODAY="$(date +%Y-%m-%d)"
FROZEN_OUT="live/frozen/${TODAY}.json"
if [ "$(date +%H)" = "15" ] && [ "$(date +%M)" -ge "45" ] && [ ! -e "$FROZEN_OUT" ]; then
    echo "[freeze] writing ${FROZEN_OUT}..."
    /usr/bin/python3 - <<PYEOF
import json
from pathlib import Path
src = Path("live/ranked/latest.json")
dst = Path("$FROZEN_OUT")
dst.parent.mkdir(parents=True, exist_ok=True)
with open(src) as f:
    d = json.load(f)
d["frozen_at"] = "15:45"
d["mock"] = False
with open(dst, "w") as f:
    json.dump(d, f, indent=2)
print(f"  ✓ froze {dst} ({len(d.get('top_picks', []))} picks)")
PYEOF
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
