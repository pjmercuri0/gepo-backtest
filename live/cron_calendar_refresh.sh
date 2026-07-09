#!/usr/bin/env bash
# Weekly Sunday-night refresh of earnings + ex-dividend calendars.
#
# Both scripts MERGE with existing CSVs (never wipe), so they're safe to run
# repeatedly. Fetches default window: today + 30 days (earnings) and today +
# 120 days (dividends). NASDAQ typically only announces ex-div dates ~30 days
# out so the dividend forward window doesn't need to be longer in practice.
cd "$(dirname "$0")/.."
mkdir -p live/logs

[ -f "$HOME/.gepo_env" ] && . "$HOME/.gepo_env"
/usr/bin/caffeinate -i -t 600 &

LOG=live/logs/calendar_refresh.log
{
  echo ""
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  echo "[1/2] Earnings calendar refresh..."
  /usr/bin/python3 fetch_earnings.py --start "$(date '+%Y-%m-%d')" --end "$(date -v+30d '+%Y-%m-%d')" --workers 8
  echo ""
  echo "[2/2] Dividend calendar refresh..."
  /usr/bin/python3 fetch_dividends.py --workers 8
} >> "$LOG" 2>&1
