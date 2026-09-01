#!/usr/bin/env bash
# Hourly :01/:31 wrapper. pull_now_parallel.sh does everything: Mya-edits
# pull + SPY intraday refresh (concurrent with the option fetchers, joined
# before the ranker) → ranker → freeze (15:01 only) → tracker → upload.
cd "$(dirname "$0")/.."
mkdir -p live/logs

# Source the user's shell env so MYA_SSH_HOST etc. are visible to cron.
[ -f live/cron_env.sh ] && . live/cron_env.sh

# Keep the Mac awake for the next 10 minutes (parallel pull takes ~3 min;
# headroom for slow groups + SPY step). caffeinate self-terminates after 600s.
/usr/bin/caffeinate -i -t 600 &

LOG=live/logs/parallel_pull.log
LOCKDIR=live/logs/cron_parallel.lock
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  {
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
    echo "SKIP: cron_parallel already running (lock: $LOCKDIR)"
  } >> "$LOG" 2>&1
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  echo "Parallel option pull (Mya-edits pull + SPY refresh run inside,"
  echo "      concurrent with fetchers; incl. 15:01 freeze + tracker + Mya upload)..."
  bash live/pull_now_parallel.sh
  # Empirical pool refresh moved OUT of the per-scan path (2026-07-27). It is a
  # weekly-by-design job: monthly_pool_refresh.py reprocesses ~25M vendor CSV
  # rows and rewrites the 15.6M-row master pool. ITM outcomes only resolve at
  # Friday expiry, so running it on every :01/:31 scan cooked the CPU 16x/day
  # for zero benefit. Now scheduled weekly via cron_pool_refresh.sh (Fri 17:31).
} >> "$LOG" 2>&1
