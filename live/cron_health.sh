#!/usr/bin/env bash
# Wrapper for the health-check cron. Runs every 5 min during market hours.
# Checks the freshness of the SPY intraday tick and emits an alert payload
# to live/notifications/ if stale. If an alert was emitted, also rsyncs to
# Mya so she picks it up immediately.
cd "$(dirname "$0")/.."
mkdir -p live/logs live/notifications
PYTHON="$PWD/.venv/bin/python"
export PATH="$PWD/.venv/bin:/usr/bin:/bin"

# Source env so MYA_SSH_HOST etc are visible to cron-spawned shells.
[ -f "$HOME/.gepo_env" ] && . "$HOME/.gepo_env"

# Count existing notification files before
BEFORE=$(ls -1 live/notifications/ 2>/dev/null | wc -l | tr -d ' ')

"$PYTHON" -m live.health_check

# Count after — if a new file appeared, ship it
AFTER=$(ls -1 live/notifications/ 2>/dev/null | wc -l | tr -d ' ')
if [ "$AFTER" -gt "$BEFORE" ] && [ -n "${MYA_SSH_HOST:-}" ]; then
  ./live/upload_to_mya.sh >> live/logs/health.log 2>&1
fi
