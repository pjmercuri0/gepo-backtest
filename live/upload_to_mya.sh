#!/usr/bin/env bash
# Rsync the live data directories from this Mac to Mya's server.
# Idempotent — rsync only transfers changed files.
#
# Configure once in ~/.zshrc (or pass inline):
#   export MYA_SSH_HOST="ubuntu@<your-ec2-ip-or-hostname>"
#   export MYA_REMOTE_BASE="/opt/vito/gepo-backtest/live"
#   export MYA_SSH_KEY="$HOME/.ssh/id_ed25519"     # optional; -i flag
#
# Run on demand:
#   ./live/upload_to_mya.sh
#
# Or have cron_spy.sh call it after each fetch.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Config
: "${MYA_SSH_HOST:?MYA_SSH_HOST not set — see header comment for setup}"
: "${MYA_REMOTE_BASE:=/opt/vito/gepo-backtest/live}"
SSH_OPTS=""
if [ -n "${MYA_SSH_KEY:-}" ]; then
  SSH_OPTS="-i $MYA_SSH_KEY"
fi

# rsync flags:
#   -a : archive (preserves timestamps, perms)
#   -z : compress in transit
#   --partial : keep partial transfers (helps on flaky links)
#   --timeout=20 : bail if no progress
#   no --delete : keep server-side history files even if pruned locally
RSYNC="rsync -az --partial --timeout=20 -e 'ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new $SSH_OPTS'"

# Sync the three live directories. Each subdir creates itself remotely.
for dir in ranked frozen notifications; do
  if [ -d "live/$dir" ]; then
    eval "$RSYNC live/$dir/ $MYA_SSH_HOST:$MYA_REMOTE_BASE/$dir/" \
      && echo "  ✓ uploaded live/$dir/" \
      || echo "  ✗ rsync failed for live/$dir/"
  fi
done
