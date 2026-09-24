#!/usr/bin/env bash
# Price every OPEN Actuals position off its own legs, every scan.
#
# Separate from the scan on purpose (user, 2026-09-24). The scanner fetches a
# strike band sized to find NEW candidates near spot, floored at +/-2%, so a
# position whose price has moved falls outside it and stops being quoted -- in
# either direction. That is what left AAPL marked at max loss and XOM at zero.
# This asks IBKR for the exact strikes held, regardless of any band.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f live/cron_env.sh ] && . live/cron_env.sh
mkdir -p live/logs

{
  echo "=== mark_actuals at $(date '+%Y-%m-%d %H:%M:%S') ==="
  "${GEPO_PYTHON:-python3}" -m live.mark_actuals
} >> live/logs/mark_actuals.log 2>&1

# Ship the marks so the site prices from them too.
if [ -n "${MYA_SSH_HOST:-}" ] && [ -f live/ranked/actuals_marks.json ]; then
  rsync -az --timeout=20 -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    live/ranked/actuals_marks.json \
    "$MYA_SSH_HOST:${MYA_REMOTE_BASE:-/opt/vito/gepo-backtest/live}/ranked/actuals_marks.json" \
    >> live/logs/mark_actuals.log 2>&1 || true
fi
