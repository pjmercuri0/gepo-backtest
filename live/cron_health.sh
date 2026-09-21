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

# ── display consistency ──────────────────────────────────────────────────────
# Every number the site shows, reconciled against its source: open P&L is
# (fill - mark), settled P&L is settle_pnl at the recorded fill, card headers
# equal the sum of their rows, marks sit inside [0, width], status badges match
# spot vs strikes, and the tabs agree on spot for the SAME spread.
#
# All of this was caught by hand on 2026-09-21 only because the user spotted the
# numbers first. It runs itself now, and writes a health alert on failure so the
# site says so rather than quietly showing a wrong P&L.
CONSIST_LOG=live/logs/consistency.log
if ! "${GEPO_PYTHON:-python3}" -m pytest -q tests/test_display_consistency.py \
        > "$CONSIST_LOG" 2>&1; then
  echo "[$(date '+%F %T')] display consistency FAILED" >> live/logs/health.log
  tail -40 "$CONSIST_LOG" >> live/logs/health.log
  "${GEPO_PYTHON:-python3}" - <<'PYEOF'
import json, re, time
from pathlib import Path
log = Path("live/logs/consistency.log").read_text()
fails = re.findall(r"AssertionError: (.+)", log)[:8]
out = Path("live/notifications") / f"health-consistency-{time.strftime('%Y-%m-%dT%H%M%S')}.json"
out.write_text(json.dumps({
    "kind": "display-consistency",
    "severity": "error",
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "message": "A number on the site does not reconcile with its source.",
    "failures": fails or ["see live/logs/consistency.log"],
}, indent=1))
print(f"[consistency] wrote {out}")
PYEOF
fi
