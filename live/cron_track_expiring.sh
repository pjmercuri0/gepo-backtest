#!/usr/bin/env bash
# Friday-only tracker for picks expiring today. Captures mark/P&L on the
# DTE-0 options that aren't in the daily snapshot (because the regular
# fetcher's DTE window is [1, 7]).
cd "$(dirname "$0")/.."
mkdir -p live/logs

[ -f "$HOME/.gepo_env" ] && . "$HOME/.gepo_env"

# Scheduled Thu+Fri; only act on the week's real settlement day so holiday-
# shifted weeks (Friday NYSE holiday → Thursday expiry) track on the right day.
if ! /usr/bin/python3 -m live.trading_calendar --is-settlement-day; then
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') not weekly settlement day — skip ===" >> live/logs/track_expiring.log
  exit 0
fi

# Keep Mac awake for 3 minutes so cron actually fires + IB fetch completes
# even if the system was sleeping when the cron schedule hit.
/usr/bin/caffeinate -i -t 180 &

LOG=live/logs/track_expiring.log
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  # Pull Mya-side actual_credit edits before tracker appends new rows,
  # so the merged file we push back includes the user's edits.
  if [ -n "${MYA_SSH_HOST:-}" ]; then
    bash live/pull_from_mya.sh 2>&1 | sed "s/^/  [Pull] /"
  fi

  TODAY="$(date '+%Y-%m-%d')"
  # Discover frozen files whose top_picks[0].expiry_date == today AND have
  # no outcome yet. At most one per trading day → max 5 per week.
  EXPIRING=()
  for f in live/frozen/*.json; do
    [ -e "$f" ] || continue
    if /usr/bin/python3 -c "
import json, sys
d = json.load(open('$f'))
if d.get('outcome'): sys.exit(1)
picks = d.get('top_picks') or []
if not picks: sys.exit(1)
exp = (picks[0].get('expiry_date') or '')[:10]
sys.exit(0 if exp == '$TODAY' else 1)
" 2>/dev/null; then
      EXPIRING+=("$f")
    fi
  done

  if [ ${#EXPIRING[@]} -eq 0 ]; then
    echo "No frozen files expiring today."
  else
    echo "Fan-out: ${#EXPIRING[@]} expiring day(s) → parallel workers"
    PIDS=()
    i=0
    for f in "${EXPIRING[@]}"; do
      DATE="$(basename "$f" .json)"
      CID=$((203 + i))
      /usr/bin/python3 -m live.track_expiring --date "$DATE" --client-id $CID \
        > "live/logs/track_${DATE}.log" 2>&1 &
      PIDS+=($!)
      i=$((i + 1))
    done
    # Wait for all workers, capture exit codes
    FAIL=0
    for pid in "${PIDS[@]}"; do
      wait "$pid" || FAIL=$((FAIL + 1))
    done
    # Inline each worker's log into the main log for unified history
    for f in "${EXPIRING[@]}"; do
      DATE="$(basename "$f" .json)"
      echo "── $DATE ──"
      cat "live/logs/track_${DATE}.log" 2>/dev/null
    done
    [ $FAIL -gt 0 ] && echo "✗ $FAIL worker(s) failed"
  fi

  if [ -n "${MYA_SSH_HOST:-}" ]; then
    bash live/upload_to_mya.sh 2>&1 | sed "s/^/  [Upload] /"
  fi
} >> "$LOG" 2>&1
