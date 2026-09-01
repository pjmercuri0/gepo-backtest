#!/usr/bin/env bash
# Daily 17:01 cron (Mon-Fri) — pull last 20 daily TRADES bars per SP100 ticker
# from IBKR, compute 10d RV, merge into output/rv_table.parquet; refresh Yahoo
# closes; then settle any expired snapshot picks.
#
# Timing: 17:01 (after 16:00 market close) so today's closing bar is finalized
# and included. Next morning's cron_parallel reads the fresh RV table.
# Self-sufficient — no vendor data dependency.
cd "$(dirname "$0")/.."
mkdir -p live/logs

# Avoid xcrun failures from a stale inherited developer-tools path.
unset DEVELOPER_DIR

# Source the user's shell env so IBKR settings etc. are visible to cron.
[ -f live/cron_env.sh ] && . live/cron_env.sh

# Keep Mac awake for the next 8 minutes (IBKR bars < 2 min + Yahoo refresh ~70s).
/usr/bin/caffeinate -i -t 480 &

LOG=live/logs/daily_bars.log
LOCKDIR=live/logs/cron_daily_bars.lock
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  {
    echo ""
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
    echo "SKIP: cron_daily_bars already running (lock: $LOCKDIR)"
  } >> "$LOG" 2>&1
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT

{
  echo ""
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  "${GEPO_PYTHON:-python3}" -m live.fetch_daily_bars
  # Refresh Yahoo daily closes (MERGE, never wipe) so snapshot_picks.settle()
  # can resolve each pick's expiry close.
  "${GEPO_PYTHON:-python3}" fetch_yahoo_recent.py
  # Settle expired snapshot picks now (post_close=True): same-day expiries
  # settle this evening off the live IB RTH close, matching the History tab,
  # instead of lagging to the next-day Yahoo "morning after". Settle-only —
  # no capture() — so a stale Friday latest.json isn't re-snapshotted.
  "${GEPO_PYTHON:-python3}" -c "from live import snapshot_picks; snapshot_picks.settle(post_close=True)"
  # Push the freshly-settled intraday_picks to Mya so the Snapshots tab shows
  # outcomes the same evening instead of waiting for the next morning's scan.
  if [ -n "${MYA_SSH_HOST:-}" ]; then
    bash live/upload_to_mya.sh 2>&1 | sed "s/^/  [Upload] /"
  fi
} >> "$LOG" 2>&1
