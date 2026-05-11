#!/usr/bin/env bash
# Wrapper for the SPY intraday fetcher + Mya rsync.
# Cron calls this so the crontab entry can be a short single path.
cd "$(dirname "$0")/.."
mkdir -p live/logs

# Source the user's shell so MYA_SSH_HOST etc. are visible to cron.
# cron environment doesn't inherit interactive-shell exports by default.
[ -f "$HOME/.gepo_env" ] && . "$HOME/.gepo_env"

LOG=live/logs/spy_intraday.log
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  /usr/bin/python3 -m live.fetch_spy_intraday
  if [ -n "${MYA_SSH_HOST:-}" ]; then
    ./live/upload_to_mya.sh
  else
    echo "  (skip upload — MYA_SSH_HOST not set)"
  fi
} >> "$LOG" 2>&1
