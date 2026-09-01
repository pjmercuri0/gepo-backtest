#!/usr/bin/env bash
# Friday 17:31 ET — refresh master_pool.parquet from the current-month vendor
# folder, after the 17:01 daily-bars/calendar jobs and this week's Friday expiry
# (ITM outcomes only resolve at Friday close). Picks up any DG_YYYYMonth/ CSVs
# uploaded since last refresh. Friday chosen so the Mac is actually awake.
cd "$(dirname "$0")/.."
mkdir -p live/logs

[ -f live/cron_env.sh ] && . live/cron_env.sh

# Hold the Mac awake for the pool rebuild (~25M CSV rows → 5-10 min). caffeinate
# self-terminates after 900s. It cannot WAKE a sleeping Mac, only keep an awake
# one from sleeping — see the pmset note in SESSION_HANDOFF if Friday-evening
# sleep ever causes a miss.
/usr/bin/caffeinate -i -t 900 &

LOG=live/logs/pool_refresh.log
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  "${GEPO_PYTHON:-python3}" monthly_pool_refresh.py
  echo ""
} >> "$LOG" 2>&1
