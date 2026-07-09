#!/usr/bin/env bash
# Pull live/frozen/*.json from Mya back to Mac.
#
# Why: the webapp on Mya has a click-to-edit endpoint that mutates
# pick.actual_credit (and derived fields). Mac is otherwise the only
# writer, so without this step, the next Mac → Mya rsync would clobber
# the user's edits.
#
# Strategy: rsync --update (-u) only overwrites a Mac file when Mya's
# copy is newer. So Mya-side edits flow back, but Mac's freshly-written
# tracking rows / new freeze files are preserved.
#
# Call this at the START of any cron that reads or writes frozen files
# (cron_parallel.sh, cron_expire.sh, cron_track_expiring.sh).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

: "${MYA_SSH_HOST:?MYA_SSH_HOST not set — see live/upload_to_mya.sh header}"
: "${MYA_REMOTE_BASE:=/opt/vito/gepo-backtest/live}"
SSH_OPTS=""
if [ -n "${MYA_SSH_KEY:-}" ]; then
  SSH_OPTS="-i $MYA_SSH_KEY"
fi

# -a archive, -z compress, -u update-only-if-newer, --partial flaky-safe
RSYNC="rsync -azu --partial --timeout=20 -e 'ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new $SSH_OPTS'"

PULL_DIRS=(
  "live/frozen/"
)

for sub in "${PULL_DIRS[@]}"; do
  src="$MYA_SSH_HOST:$MYA_REMOTE_BASE/${sub#live/}"
  mkdir -p "$sub"
  if eval "$RSYNC \"$src\" \"$sub\""; then
    echo "  ✓ pulled $sub"
  else
    echo "  ✗ rsync failed for $sub"
  fi
done
