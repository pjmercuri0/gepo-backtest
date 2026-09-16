#!/usr/bin/env bash
# 09:36 and 10:06 Mon-Fri: today's market opening gap for the P_real drift (handoff §0.45).
# The second run is a retry and exits at once if 09:36 already stored today's value.
cd "$(dirname "$0")/.."
mkdir -p live/logs
export PATH="$PWD/.venv/bin:/usr/bin:/bin"
unset DEVELOPER_DIR
[ -f live/cron_env.sh ] && . live/cron_env.sh
/usr/bin/caffeinate -i -t 900 &
LOG=live/logs/market_gap.log
LOCKDIR=live/logs/cron_market_gap.lock
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  { echo ""; echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="; echo "SKIP: cron_market_gap already running"; } >> "$LOG" 2>&1
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT
{
  echo ""
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  "${GEPO_PYTHON:-python3}" -m live.fetch_market_gap
} >> "$LOG" 2>&1
