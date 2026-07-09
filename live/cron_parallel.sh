#!/usr/bin/env bash
# Hourly :01/:31 wrapper. pull_now_parallel.sh does everything: Mya-edits
# pull + SPY intraday refresh (concurrent with the option fetchers, joined
# before the ranker) → ranker → freeze (15:01 only) → tracker → upload.
cd "$(dirname "$0")/.."
mkdir -p live/logs

# Source the user's shell env so MYA_SSH_HOST etc. are visible to cron.
[ -f "$HOME/.gepo_env" ] && . "$HOME/.gepo_env"

# Cron/launchd can inherit a stale DEVELOPER_DIR. If it points at a removed
# full Xcode app, Apple's /usr/bin/python3 fails via xcrun before our code runs.
unset DEVELOPER_DIR

# Keep the Mac awake for the next 10 minutes (parallel pull takes ~3 min;
# headroom for slow groups + SPY step). caffeinate self-terminates after 600s.
/usr/bin/caffeinate -i -t 600 &

LOG=live/logs/parallel_pull.log
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  echo "[1/2] Parallel option pull (Mya-edits pull + SPY refresh run inside,"
  echo "      concurrent with fetchers; incl. 15:01 freeze + tracker + Mya upload)..."
  bash live/pull_now_parallel.sh
  echo "[2/2] Empirical pool refresh (idempotent; no-op if no new vendor data)..."
  /usr/bin/python3 monthly_pool_refresh.py 2>&1 | sed "s/^/  [Pool] /"
} >> "$LOG" 2>&1
