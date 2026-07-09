#!/usr/bin/env bash
# Friday 15:01 — classify expiring picks as MUST_CLOSE (pin zone or ITM)
# vs SAFE_EXPIRE (cushion ≥0.5% above/below short strike), and emit
# close-debit recommendations for MUST_CLOSE only. SAFE_EXPIRE picks are
# left to expire and keep the full credit.
#
# Output: live/notifications/close_alert_YYYY-MM-DD.json with per-pick
# status + rec_debit. Re-runnable: schedule additional firings later in
# the afternoon if you want fresh classification as spot drifts.
cd "$(dirname "$0")/.."
mkdir -p live/logs

[ -f "$HOME/.gepo_env" ] && . "$HOME/.gepo_env"

# Scheduled Thu+Fri; only act on the week's real settlement day so holiday-
# shifted weeks (Friday NYSE holiday → Thursday expiry) alert on the right day.
if ! /usr/bin/python3 -m live.trading_calendar --is-settlement-day; then
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') not weekly settlement day — skip ===" >> live/logs/close_alert.log
  exit 0
fi

/usr/bin/caffeinate -i -t 120 &

LOG=live/logs/close_alert.log
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  if [ -n "${MYA_SSH_HOST:-}" ]; then
    bash live/pull_from_mya.sh 2>&1 | sed "s/^/  [Pull] /"
  fi
  /usr/bin/python3 -m live.close_alert
  if [ -n "${MYA_SSH_HOST:-}" ]; then
    bash live/upload_to_mya.sh 2>&1 | sed "s/^/  [Upload] /"
  fi
} >> "$LOG" 2>&1
