#!/usr/bin/env bash
# Daily settler wrapper. Fires once a day at 16:01 ET, right after market
# close. Settles any frozen files whose expiry has passed by looking up the
# underlying close price via ib_insync. Idempotent — re-runs are safe.
cd "$(dirname "$0")/.."
mkdir -p live/logs
PYTHON="$PWD/.venv/bin/python"
export PATH="$PWD/.venv/bin:/usr/bin:/bin"

[ -f "$HOME/.gepo_env" ] && . "$HOME/.gepo_env"

# Scheduled Thu+Fri; only act on the week's real settlement day so holiday-
# shifted weeks (Friday NYSE holiday → Thursday expiry) settle on the right day.
if ! "$PYTHON" -m live.trading_calendar --is-settlement-day; then
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') not weekly settlement day — skip ===" >> live/logs/expire.log
  exit 0
fi

# Brief caffeinate to keep the Mac awake long enough to finish (~10 sec).
/usr/bin/caffeinate -i -t 120 &

LOG=live/logs/expire.log
LOCKDIR=live/logs/cron_expire.lock
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  {
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
    echo "SKIP: cron_expire already running (lock: $LOCKDIR)"
  } >> "$LOG" 2>&1
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  # Pull Mya-side actual_credit edits before settling, so actual P&L
  # in the outcome block reflects real broker fills.
  if [ -n "${MYA_SSH_HOST:-}" ]; then
    bash live/pull_from_mya.sh 2>&1 | sed "s/^/  [Pull] /"
  fi
  "$PYTHON" -m live.expire_frozen
  if [ -n "${MYA_SSH_HOST:-}" ]; then
    bash live/upload_to_mya.sh 2>&1 | sed "s/^/  [Upload] /"
  fi
} >> "$LOG" 2>&1
