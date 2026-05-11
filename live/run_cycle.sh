#!/usr/bin/env bash
# One fetch+rank cycle. Wire this into cron / launchd every 15 min during
# market hours. Logs to live/logs/YYYY-MM-DD.log.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/live/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d).log"

{
  echo "=== cycle start $(date '+%Y-%m-%d %H:%M:%S') ==="
  python3 -m live.fetcher 2>&1
  python3 -m live.ranker  2>&1
  echo "=== cycle end   $(date '+%Y-%m-%d %H:%M:%S') ==="
  echo
} >> "$LOG" 2>&1
