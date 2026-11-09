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
    # ETFs (2026-09-20). SPY/QQQ/IWM are the #1/#2/#34 names in the backtest --
    # 637 of 4,846 trades, 13.1% of them and 15.5% of its P&L -- but were never
    # in the live universe, so the published book was not measuring the strategy
    # actually being run.
    # Ordinary US equity options, physically settled; same Stock path as the rest.
    "SPY" "QQQ" "IWM"
)

# Eight IB clients (100-107), each running its tickers concurrently via
# asyncio.gather inside fetcher.py. This keeps groups smaller/faster than the
# conservative 6-client setting while staying below the old 10-client burst
# that repeatedly timed out during Gateway's account/execution-sync handshake.
GROUP_SIZE=13
NUM_GROUPS=8

# The fetch loop slices TICKERS into NUM_GROUPS blocks of GROUP_SIZE. If the list
# is longer than GROUP_SIZE*NUM_GROUPS the tail is SILENTLY DROPPED -- no error,
# no log line, the names just never get scanned. Fail loudly instead.
if [ "${#TICKERS[@]}" -gt "$((GROUP_SIZE * NUM_GROUPS))" ]; then
    echo "FATAL: ${#TICKERS[@]} tickers but only $((GROUP_SIZE * NUM_GROUPS)) slots" \
         "(GROUP_SIZE=$GROUP_SIZE x NUM_GROUPS=$NUM_GROUPS)." >&2
    echo "       Raise GROUP_SIZE or NUM_GROUPS; the tail would be dropped without warning." >&2
    exit 1
fi

NOW="$(date '+%Y-%m-%d')"
HHMM="$(date '+%H%M')"
DATE_DIR="live/snapshots/$NOW"
mkdir -p "$DATE_DIR"

echo "=== Parallel pull at $(date '+%Y-%m-%d %H:%M:%S') ==="

# "scanning" badge on the live tab (user 2026-11-09): a status file flagged at start and
# cleared at exit (the trap also fires on a failed scan), pushed to Mya each time. Epoch
# seconds so the webapp can age it out if a push ever fails to clear.
_scan_status() {
    printf '{"scanning": %s, "started": "%s", "started_epoch": %s, "hhmm": "%s", "updated_epoch": %s}\n' \
        "$1" "$_SCAN_START_ISO" "$_SCAN_T0" "$HHMM" "$(date +%s)" > live/ranked/scan_status.json
    if [ -n "${MYA_SSH_HOST:-}" ]; then
        rsync -az --timeout=10 -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
            live/ranked/scan_status.json \
            "$MYA_SSH_HOST:${MYA_REMOTE_BASE:-/opt/vito/gepo-backtest/live}/ranked/scan_status.json" \
            >/dev/null 2>&1 || true
    fi
}
_SCAN_T0="$(date +%s)"
_SCAN_START_ISO="$(date '+%Y-%m-%dT%H:%M:%S')"
mkdir -p live/ranked
_scan_status true &
trap '_scan_status false' EXIT

# Pull Mya-side edits while the option fetchers run. The SPY IBKR connection
# also runs concurrently (launched below, after the fetcher stagger window).
(
    if [ -n "${MYA_SSH_HOST:-}" ]; then
        bash live/pull_from_mya.sh 2>&1 | sed "s/^/  [Pull] /"
    fi
) &
PRELUDE_PID=$!

FETCH_BATCH_SIZE="$("${GEPO_PYTHON:-python3}" -c 'from live import live_config; print(live_config.FETCH_BATCH_SIZE)')"
echo "Starting $NUM_GROUPS parallel fetchers (batch size $FETCH_BATCH_SIZE)..."

# Persistent fetchers (2026-11-09): one daemon per group holds its Gateway connection between
# scans (live/fetcher.py serve()); the login that cost G5-G8 15-18s per scan happens once. Start
# any daemon that is not alive, then hand each group its job through a trigger file and wait for
# the done file. A dead or late daemon falls back to the one-shot fetcher (clientId 120+g).
FETCHD_DIR=live/logs/fetchd
mkdir -p "$FETCHD_DIR"
"${GEPO_PYTHON:-python3}" - "$NUM_GROUPS" "$FETCHD_DIR" "${GEPO_PYTHON:-python3}" <<'PYEOF'
import os, sys, subprocess
from pathlib import Path
n, d, py = int(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
for g in range(n):
    pidf = d / f"g{g}.pid"; alive = False
    if pidf.exists():
        try:
            os.kill(int(pidf.read_text().strip()), 0); alive = True
        except Exception:
            pidf.unlink(missing_ok=True)
    if not alive:
        for f in (d / f"g{g}.trigger", d / f"g{g}.done"):
            f.unlink(missing_ok=True)
        subprocess.Popen([py, "-m", "live.fetcher", "--serve", "--group", str(g), "--client-id", str(100 + g)],
                         stdout=open(d / f"g{g}.daemon.log", "a"), stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True, cwd=os.getcwd())
        print(f"  [G{g+1}] started fetch daemon (clientId={100 + g})", flush=True)
# SPY tick daemon (live/fetch_spy_intraday.py serve()): its fresh login during the fetch
# burst timed out on both attempts on 5 scans of 2026-11-09, leaving a stale tick.
pidf = d / "spy.pid"; alive = False
if pidf.exists():
    try:
        os.kill(int(pidf.read_text().strip()), 0); alive = True
    except Exception:
        pidf.unlink(missing_ok=True)
if not alive:
    for f in (d / "spy.trigger", d / "spy.done"):
        f.unlink(missing_ok=True)
    subprocess.Popen([py, "-m", "live.fetch_spy_intraday", "--serve"],
                     stdout=open(d / "spy.daemon.log", "a"), stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, start_new_session=True, cwd=os.getcwd())
    print("  [SPY] started spy tick daemon (clientId=12)", flush=True)
PYEOF
FETCH_WAIT_HALFSECS="$("${GEPO_PYTHON:-python3}" -c 'from live import live_config; print(2 * (int(live_config.FETCH_PER_TICKER_TIMEOUT) + 45))')"

PIDS=()
GROUP_FILES=()
for g in $(seq 0 $((NUM_GROUPS - 1))); do
    START=$((g * GROUP_SIZE))
    GROUP_TICKERS=("${TICKERS[@]:$START:$GROUP_SIZE}")
    CLIENT_ID=$((100 + g))
    GROUP_OUT="$DATE_DIR/${HHMM}_g${g}.parquet"
    GROUP_FILES+=("$GROUP_OUT")

    (
        TRIG="$FETCHD_DIR/g${g}.trigger"; DONE="$FETCHD_DIR/g${g}.done"; JLOG="$FETCHD_DIR/g${g}.${HHMM}.log"
        rm -f "$DONE" "$JLOG"
        printf '%s\n' "${GROUP_TICKERS[@]}" | "${GEPO_PYTHON:-python3}" -c \
            'import json,sys; print(json.dumps({"tickers": sys.stdin.read().split(), "out": sys.argv[1], "hhmm": sys.argv[2], "log": sys.argv[3]}))' \
            "$GROUP_OUT" "$HHMM" "$JLOG" > "$TRIG.tmp" && mv "$TRIG.tmp" "$TRIG"
        echo "  [G$((g+1))] Fetching ${#GROUP_TICKERS[@]} tickers via fetch daemon (clientId=$CLIENT_ID)..."
        waited=0; ok=0
        while [ "$waited" -lt "$FETCH_WAIT_HALFSECS" ]; do
            if [ -f "$DONE" ]; then ok=1; break; fi
            if ! kill -0 "$(cat "$FETCHD_DIR/g${g}.pid" 2>/dev/null || echo 0)" 2>/dev/null; then break; fi
            sleep 0.5; waited=$((waited + 1))
        done
        if [ "$ok" = 1 ]; then
            sed "s/^/  [G$((g+1))] /" "$JLOG" 2>/dev/null || true
            STATUS="$("${GEPO_PYTHON:-python3}" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status", 1))' "$DONE" 2>/dev/null || echo 1)"
            rm -f "$JLOG" "$DONE"
            exit "$STATUS"
        fi
        echo "  [G$((g+1))] ✗ fetch daemon dead or late ($((waited / 2))s) — one-shot fallback (clientId=$((120 + g)))"
        rm -f "$TRIG"
        kill "$(cat "$FETCHD_DIR/g${g}.pid" 2>/dev/null || echo 0)" 2>/dev/null || true
        rm -f "$FETCHD_DIR/g${g}.pid"
        "${GEPO_PYTHON:-python3}" -m live.fetcher --tickers "${GROUP_TICKERS[@]}" \
                                --client-id "$((120 + g))" \
                                --out "$GROUP_OUT" 2>&1 | sed "s/^/  [G$((g+1))] /"
    ) &
    PIDS+=($!)
done

# SPY intraday tick: its own IBKR connection (clientId 12). Until 2026-11-09 it
# started only after every option fetcher had exited, which put its ~18s
# (python start + connect + snapshot) serially on the critical path of every
# scan. It now launches once the last fetcher's stagger has passed plus a few
# seconds, so it never collides with the eight client handshakes but runs
# while the fetchers are in their quote phase; it is joined below, before the
# merge. The ranker only reads spy_intraday.json after the merge.
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
SPY_DELAY=0
# 2026-11-09: the tick runs on the spy daemon's held connection (trigger/done files, as the
# fetch groups). A dead or late daemon falls back to the one-shot (fresh login).
SPY_TRIG="$FETCHD_DIR/spy.trigger"; SPY_DONE="$FETCHD_DIR/spy.done"
rm -f "$SPY_DONE"
printf '{"log": "%s", "hhmm": "%s"}\n' "$SPY_LOG" "$HHMM" > "$SPY_TRIG.tmp" && mv "$SPY_TRIG.tmp" "$SPY_TRIG"
echo "Refreshing SPY intraday tick via spy daemon (clientId=12, hard timeout ${SPY_TIMEOUT}s)..."
(
    waited=0
    while [ "$waited" -lt $((SPY_TIMEOUT * 2)) ]; do
        if [ -f "$SPY_DONE" ]; then
            exit "$("${GEPO_PYTHON:-python3}" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status", 1))' "$SPY_DONE" 2>/dev/null || echo 1)"
        fi
        if ! kill -0 "$(cat "$FETCHD_DIR/spy.pid" 2>/dev/null || echo 0)" 2>/dev/null; then break; fi
        sleep 0.5; waited=$((waited + 1))
    done
    echo "  ✗ spy daemon dead or late ($((waited / 2))s) — one-shot fallback (clientId=12)" >> "$SPY_LOG"
    rm -f "$SPY_TRIG"
    kill "$(cat "$FETCHD_DIR/spy.pid" 2>/dev/null || echo 0)" 2>/dev/null || true
    rm -f "$FETCHD_DIR/spy.pid"
    exec "${GEPO_PYTHON:-python3}" -m live.fetch_spy_intraday >> "$SPY_LOG" 2>&1
) &
SPY_PID=$!
(
    sleep "$(python3 -c "print($SPY_DELAY + $SPY_TIMEOUT)")"
    if kill -0 "$SPY_PID" 2>/dev/null; then
        echo "watchdog: SPY fetch exceeded ${SPY_TIMEOUT}s — killing pid $SPY_PID" >> "$SPY_LOG"
        kill -TERM "$SPY_PID" 2>/dev/null || true
        sleep 3
        kill -KILL "$SPY_PID" 2>/dev/null || true
    fi
) &
SPY_WATCH_PID=$!

# Wait for all fetchers
for pid in "${PIDS[@]}"; do
    wait "$pid" || echo "  ✗ fetcher pid $pid exited non-zero (continuing)"
done
echo "✓ All fetchers completed ($(date +%H:%M:%S))"

# Mya-side actual-credit edits must land before the upload at the end of run.
wait "$PRELUDE_PID" || echo "  ✗ Mya pull exited non-zero (continuing)"

# Join the SPY tick (normally already done by now).
wait "$SPY_PID" 2>/dev/null || echo "  ✗ SPY refresh failed or timed out (continuing with last valid tick)"
# Stop the watchdog early if the fetch already returned, then reap it.
kill -TERM "$SPY_WATCH_PID" 2>/dev/null || true
wait "$SPY_WATCH_PID" 2>/dev/null || true
sed "s/^/  [SPY] /" "$SPY_LOG" 2>/dev/null || true
rm -f "$SPY_LOG"

# Keep the combo quote stream alive (2026-09-16; moved ahead of the merge 2026-10-01 so the
# pre-open 09:00/09:15 runs, which exit before the ranker, still relaunch it after the
# gateway's nightly reconnect). It holds IBKR subscriptions open so
# the live tab shows quotes a second old instead of a scan old; it exits immediately if
# another copy holds the lock, so this is a no-op when it is already up. No crontab change.
"${GEPO_PYTHON:-python3}" -c "
import os,sys,subprocess
from pathlib import Path
lock=Path('live/logs/combo_stream.lock')
alive=False
if lock.exists():
    try: os.kill(int(lock.read_text().strip()),0); alive=True
    except Exception: lock.unlink(missing_ok=True)
if not alive:
    subprocess.Popen([sys.executable,'-m','live.combo_stream'],
                     stdout=open('live/logs/combo_stream.log','a'),
                     stderr=subprocess.STDOUT, start_new_session=True)
    print('  [stream] started combo_stream')
else:
    print('  [stream] combo_stream already running')
"

# Merge all per-group parquets into the canonical HHMM.parquet for the ranker.
FINAL_OUT="$DATE_DIR/${HHMM}.parquet"
echo "Merging group parquets into $FINAL_OUT... ($(date +%H:%M:%S))"
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
# Manifest for the merged snapshot. Nothing gates on it any more (2026-09-20),
# but fetcher.py only writes one per GROUP, and the merged file is the one the
# ranker actually reads -- so without this the snapshot you'd want to inspect
# after a bad scan is the one with no provenance at all.
sys.path.insert(0, "$ROOT")
from live.provenance import snapshot_manifest, write_manifest
final = Path("$FINAL_OUT")
write_manifest(snapshot_manifest(merged, final, source="IBKR"), final)
print(f"  ✓ merged {len(existing)} files → {len(merged)} unique rows (+ manifest)")
for p in existing:
    p.unlink()
    # fetcher.py writes a sidecar manifest per group; drop it with its parquet
    # or every scan leaves 8 orphans behind (8 x 26 scans = ~208 files/day).
    p.with_suffix(".manifest.json").unlink(missing_ok=True)
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

echo "Running ranker... ($(date +%H:%M:%S))"
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

# Assignment-risk watch. Fetches each OPEN POSITION's own strikes directly from
# IB rather than reading the snapshot: the scan fetches a band around current
# spot, so a position goes invisible exactly when it moves deep ITM, which is
# when assignment risk appears. DE's 647.50 strike sat 7.6% from spot on
# 2026-09-02 and was absent from every snapshot that day.
"${GEPO_PYTHON:-python3}" -m live.assignment_risk --quiet 2>&1 | sed "s/^/  [Assign] /" || echo "  ✗ assignment check failed (non-fatal)"

# Push to Mya if SSH host configured. Soft-fail so a network blip
# doesn't kill the cron exit code.
UPLOAD_PID=""
if [ -n "${MYA_SSH_HOST:-}" ]; then
    echo "Uploading to Mya... ($(date +%H:%M:%S))"
    # Merge Mya-side edits first (the marks read the merged actuals.json), then push files and
    # mark positions side by side (2026-11-09; they ran one after the other, ~20s).
    UPLOAD_PHASE=merge bash live/upload_to_mya.sh 2>&1 | sed "s/^/  [Upload] /" || echo "  ✗ upload merge failed (non-fatal)"
    (
        UPLOAD_PHASE=rsync bash live/upload_to_mya.sh 2>&1 | sed "s/^/  [Upload] /" || echo "  ✗ upload failed (non-fatal)"
    ) &
    UPLOAD_PID=$!
else
    echo "  (skip Mya upload — MYA_SSH_HOST not set)"
fi

# Price every OPEN Actuals position off ITS OWN legs. Last step on purpose: the
# fetchers have disconnected by now, so it is not competing for the Gateway.
#
# Needed because the scan's strike band is sized to find NEW candidates near
# spot -- 1 sigma x sqrt(DTE/365), floored at +/-2% -- so a position whose price
# has moved falls outside it and stops being quoted, in EITHER direction. With
# no quote and no IV the webapp fell through to INTRINSIC, which below both
# strikes is the full width: AAPL 342.5/340 marked at max loss against a real
# 1.80 market, XOM marked at zero (2026-09-24).
#
# Soft-fail: a marking problem must never fail the scan.
echo "Marking open Actuals positions... ($(date +%H:%M:%S))"
"${GEPO_PYTHON:-python3}" -m live.mark_actuals 2>&1 | sed "s/^/  [Marks] /" \
    || echo "  ✗ mark_actuals failed (non-fatal)"
# The debit book (gepo-debit-ticker, live/stores.py): same marks, its own store and file.
if [ -f live/actuals_debit.json ]; then
    "${GEPO_PYTHON:-python3}" -m live.mark_actuals --store actuals_debit.json 2>&1 | sed "s|^|  [Marks/debit] |" \
        || echo "  ✗ mark_actuals (debit) failed (non-fatal)"
fi
# The rsync phase also pushes the marks files (stale copies); wait for it, then push the fresh ones.
if [ -n "$UPLOAD_PID" ]; then wait "$UPLOAD_PID" || true; fi
echo "✓ scan complete ($(date +%H:%M:%S))"
for _mf in actuals_marks.json actuals_marks_debit.json; do
  if [ -n "${MYA_SSH_HOST:-}" ] && [ -f "live/ranked/$_mf" ]; then
    rsync -az --timeout=20 -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
        "live/ranked/$_mf" \
        "$MYA_SSH_HOST:${MYA_REMOTE_BASE:-/opt/vito/gepo-backtest/live}/ranked/$_mf" \
        && echo "  [Marks] ✓ uploaded $_mf" || echo "  [Marks] ✗ upload failed $_mf (non-fatal)"
  fi
done
