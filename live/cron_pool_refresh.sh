#!/usr/bin/env bash
# Sunday 15:01 ET — refresh master_pool.parquet from current-month vendor folder.
# Picks up any DG_YYYYMonth/ CSVs uploaded since last refresh.
cd "$(dirname "$0")/.."
mkdir -p live/logs

[ -f "$HOME/.gepo_env" ] && . "$HOME/.gepo_env"

LOG=live/logs/pool_refresh.log
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  /usr/bin/python3 monthly_pool_refresh.py
  echo ""
} >> "$LOG" 2>&1
