#!/usr/bin/env bash
# Daily 16:31 cron — pull last 20 daily TRADES bars per SP100 ticker from IBKR,
# compute 10d RV, merge into output/rv_table.parquet.
#
# Timing: 16:31 (31 min after 16:00 market close) so today's closing bar is
# finalized and included. Next morning's cron_parallel reads the fresh RV table.
# Self-sufficient — no vendor data dependency.
cd "$(dirname "$0")/.."
mkdir -p live/logs

# Source the user's shell env so IBKR settings etc. are visible to cron.
[ -f "$HOME/.gepo_env" ] && . "$HOME/.gepo_env"

# Keep Mac awake for the next 8 minutes (IBKR bars < 2 min + Yahoo refresh ~70s).
/usr/bin/caffeinate -i -t 480 &

LOG=live/logs/daily_bars.log
{
  echo ""
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  /usr/bin/python3 -m live.fetch_daily_bars
  # Refresh Yahoo daily closes (MERGE, never wipe) so snapshot_picks.settle()
  # can resolve each pick's expiry close on the next morning's scan.
  /usr/bin/python3 fetch_yahoo_recent.py
} >> "$LOG" 2>&1
