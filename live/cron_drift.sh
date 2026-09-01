#!/usr/bin/env bash
# 15:55 wrapper (pre-close). Re-fetches and re-ranks 10 min after the
# 15:45 freeze, then drifts today's frozen picks to the fresh metrics.
# The selection is locked at 15:45; only the entry-time metric fields on
# those 5 picks get overwritten. Was previously 16:00 sharp, but every
# post-close IB fetch returns "No security definition" errors — moved
# pre-close so the fetch is reliable, at the cost of not capturing the
# actual 16:00 close (IBKR 15-min delay means 15:55 wall-clock sees
# ~15:40 actual market data).
cd "$(dirname "$0")/.."
mkdir -p live/logs

[ -f live/cron_env.sh ] && . live/cron_env.sh

# Keep Mac awake long enough for the full pipeline (~3 min parallel pull
# + ~10s ranker + ~5s drift + ~5s tracker + upload).
/usr/bin/caffeinate -i -t 600 &

LOG=live/logs/drift.log
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  echo "[1/4] SPY intraday refresh..."
  "${GEPO_PYTHON:-python3}" -m live.fetch_spy_intraday
  echo "[2/4] Parallel option pull + ranker..."
  # pull_now_parallel.sh runs fetch+merge+ranker+tracker+upload. The freeze
  # branch only fires when $(date +%H) == 15, so it's a no-op at 16:00. We
  # still let it run the tracker since it'll mark all active frozen files;
  # then we drift today's file and run the tracker AGAIN so today's drifted
  # entry_credit feeds into the 16:00 P&L row.
  bash live/pull_now_parallel.sh
  echo "[3/4] Drift today's frozen picks to 15:55 metrics..."
  "${GEPO_PYTHON:-python3}" -m live.drift_frozen --drift-at 15:55 2>&1 | sed "s/^/  [Drift] /"
  echo "[4/4] Re-running MTM tracker so drifted entry_credit feeds the 15:55 mark..."
  "${GEPO_PYTHON:-python3}" -m live.track_frozen 2>&1 | sed "s/^/  [Tracker] /"
  if [ -n "${MYA_SSH_HOST:-}" ]; then
    bash live/upload_to_mya.sh 2>&1 | sed "s/^/  [Upload] /"
  fi
} >> "$LOG" 2>&1
