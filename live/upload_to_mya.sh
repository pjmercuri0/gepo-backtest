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

# Selective upload — only ship files we generate from real IBKR data.
# Mya's server generates its own mock latest.json / frozen/*.json on cron
# until OPRA is live; if we rsync our (stale) mock over hers, the regime
# banner goes stale.
#
# Right now we only have real intraday SPY ticks. Once the option ranker
# is producing real ranked output, add `live/ranked/latest.json` and the
# frozen/notifications dirs back to this list.
UPLOAD_FILES=(
  "live/ranked/spy_intraday.json"   # always real (IBKR live tick)
  # "live/ranked/latest.json"       # uncomment when ranker is live
  # "live/frozen/"                  # uncomment when freezer is wired
  # "live/notifications/"           # uncomment when notifications fire
)

for src in "${UPLOAD_FILES[@]}"; do
  if [ ! -e "$src" ]; then
    echo "  · skip $src (not present)"
    continue
  fi
  dest="$MYA_SSH_HOST:$MYA_REMOTE_BASE/${src#live/}"
  eval "$RSYNC \"$src\" \"$dest\"" \
    && echo "  ✓ uploaded $src" \
    || echo "  ✗ rsync failed for $src"
done
