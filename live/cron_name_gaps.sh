#!/usr/bin/env bash
# 09:36 and 10:06 Mon-Fri: today's per-stock opening gaps for the own-gap P_real drift (handoff §0.45).
# The second run is a retry: it fetches only names still missing today's gap.
cd "$(dirname "$0")/.."
mkdir -p live/logs
export PATH="$PWD/.venv/bin:/usr/bin:/bin"
unset DEVELOPER_DIR
[ -f live/cron_env.sh ] && . live/cron_env.sh
/usr/bin/caffeinate -i -t 900 &
LOG=live/logs/name_gaps.log
LOCKDIR=live/logs/cron_name_gaps.lock
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  { echo ""; echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="; echo "SKIP: cron_name_gaps already running"; } >> "$LOG" 2>&1
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT
{
  echo ""
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  "${GEPO_PYTHON:-python3}" -m live.fetch_name_gaps
} >> "$LOG" 2>&1
