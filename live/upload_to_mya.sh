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

# Always merge Mya-side actual_credit edits into Mac's frozen files BEFORE
# uploading. Otherwise any edit user made between our last pull and this
# upload gets clobbered. Race destroys real fill records. See error #59+.
echo "  → preserving Mya-side actual_credit edits..."
python3 - <<'PYEOF'
import json, os, subprocess, sys, tempfile
from pathlib import Path
host = os.environ['MYA_SSH_HOST']
remote_base = os.environ.get('MYA_REMOTE_BASE', '/opt/vito/gepo-backtest/live')
local_frozen = Path('live/frozen')
ssh_key = os.environ.get('MYA_SSH_KEY', '')
ssh_opts = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new']
if ssh_key: ssh_opts += ['-i', ssh_key]
preserved = 0
for fp in local_frozen.glob('*.json'):
    name = fp.name
    # Pull just this file's actual_credit values from Mya
    try:
        out = subprocess.check_output(['ssh'] + ssh_opts + [host,
            f"python3 -c \"import json; j=json.load(open('{remote_base}/frozen/{name}')); print(json.dumps({{p['ticker']: p.get('actual_credit') for p in j.get('top_picks', []) if p.get('actual_credit') is not None}}))\""
        ], stderr=subprocess.DEVNULL, timeout=15).decode().strip()
        mya_edits = json.loads(out) if out else {}
    except Exception:
        continue
    if not mya_edits:
        continue
    local = json.loads(fp.read_text())
    changed = False
    for p in local.get('top_picks', []):
        tk = p.get('ticker')
        if tk in mya_edits and p.get('actual_credit') != mya_edits[tk]:
            ac = mya_edits[tk]
            p['actual_credit'] = ac
            # Recompute actual_max_loss
            mc = float(p.get('net_credit') or 0)
            ml = float(p.get('max_loss') or 0)
            spread_w = mc + ml
            p['actual_max_loss'] = round(spread_w - ac, 4)
            changed = True
            preserved += 1
    if changed:
        # Atomic write
        tmp = fp.with_suffix('.tmp')
        tmp.write_text(json.dumps(local, indent=2))
        tmp.replace(fp)
        print(f"    ✓ preserved {sum(1 for k in mya_edits if k in {p['ticker'] for p in local.get('top_picks',[])})} edit(s) in {name}")
print(f"  → preserved {preserved} actual_credit edit(s) total")
PYEOF

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
  "live/ranked/latest.json"         # real ranked picks (OPRA live as of 2026-05-20)
  "live/notifications/"             # health alerts and (later) 15:45 freeze payloads
  "live/frozen/"                    # daily 15:45 freeze snapshots (History tab)
  "live/intraday_picks/"            # every :01/:31 scan's qualified picks (Snapshots tab)
  "live/data/backtest_equity.json"  # precomputed equity curve (Backtest tab)
  "live/data/oot_equity.json"       # 2026 out-of-time equity curve (OOT tab)
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
