#!/usr/bin/env bash
# Wrapper for the health-check cron. Runs every 5 min during market hours.
# Checks the freshness of the SPY intraday tick and emits an alert payload
# to live/notifications/ if stale. If an alert was emitted, also rsyncs to
# Mya so she picks it up immediately.
cd "$(dirname "$0")/.."
mkdir -p live/logs live/notifications

# Source env so MYA_SSH_HOST etc are visible to cron-spawned shells.
[ -f live/cron_env.sh ] && . live/cron_env.sh
: "${MYA_REMOTE_BASE:=/opt/vito/gepo-backtest/live}"

# Capture existing health alerts before the check. Only newly-created health
# files are uploaded; normal live/upload_to_mya.sh intentionally excludes them.
BEFORE=$(mktemp)
AFTER=$(mktemp)
trap 'rm -f "$BEFORE" "$AFTER"' EXIT
find live/notifications -maxdepth 1 -name 'health-*.json' -type f -print | sort > "$BEFORE"

"${GEPO_PYTHON:-python3}" -m live.health_check

# Upload only newly created health alerts.
find live/notifications -maxdepth 1 -name 'health-*.json' -type f -print | sort > "$AFTER"
if [ -n "${MYA_SSH_HOST:-}" ]; then
  comm -13 "$BEFORE" "$AFTER" | while IFS= read -r alert; do
    [ -n "$alert" ] || continue
    rsync -az --partial --timeout=20 -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new ${MYA_SSH_KEY:+-i $MYA_SSH_KEY}" \
      "$alert" "$MYA_SSH_HOST:$MYA_REMOTE_BASE/notifications/" >> live/logs/health.log 2>&1
  done
fi
