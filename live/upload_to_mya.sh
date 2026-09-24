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

[ -f live/cron_env.sh ] && . live/cron_env.sh

# --- Non-production guard -------------------------------------------------
# Only ONE machine may publish to Mya. When the pipeline moved to the Mac mini
# (2026-08-20), the retired MacBook Air kept firing scans; each failed scan fell
# through to the "upload SPY tick only" branch and rsynced its stale Aug-19 tick
# over the mini's live quote — three times in one morning before it was caught.
#
# Deliberately FAIL-OPEN: this blocks only when live/NOT_PRODUCTION exists, so a
# machine that pulls this change keeps publishing normally. Drop the marker file
# on any host that must never publish. Override for a one-off with
# GEPO_FORCE_UPLOAD=1.
if [ -f live/NOT_PRODUCTION ] && [ "${GEPO_FORCE_UPLOAD:-0}" != "1" ]; then
  echo "REFUSING to upload: live/NOT_PRODUCTION marker present on $(hostname -s)." >&2
  echo "  This host is not the publisher; uploading would overwrite production data." >&2
  [ -s live/NOT_PRODUCTION ] && sed 's/^/  | /' live/NOT_PRODUCTION >&2
  echo "  Override with: GEPO_FORCE_UPLOAD=1 bash live/upload_to_mya.sh" >&2
  exit 1
fi
# --------------------------------------------------------------------------

# Config
: "${MYA_SSH_HOST:?MYA_SSH_HOST not set — see header comment for setup}"
: "${MYA_REMOTE_BASE:=/opt/vito/gepo-backtest/live}"
SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
if [ -n "${MYA_SSH_KEY:-}" ]; then
  printf -v _escaped_key '%q' "$MYA_SSH_KEY"
  SSH_COMMAND+=" -i ${_escaped_key}"
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
# ONE ssh for the whole frozen directory. This used to open a connection per
# file, and at 61 frozen files x ~0.5s of SSH handshake that was ~30s — the
# entire cost of the upload step, since the rsyncs themselves total under 4s.
_REMOTE = r"""
import json, glob, os, sys
out = {}
for fp in glob.glob(sys.argv[1] + "/frozen/*.json"):
    try:
        j = json.load(open(fp))
    except Exception:
        continue
    edits = {p["ticker"]: p.get("actual_credit")
             for p in (j.get("top_picks") or [])
             if p.get("actual_credit") is not None}
    if edits:
        out[os.path.basename(fp)] = edits
print(json.dumps(out))
"""
try:
    _r = subprocess.run(['ssh'] + ssh_opts + [host, 'python3 - ' + remote_base],
                        input=_REMOTE.encode(), stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, timeout=60)
    _out = _r.stdout.decode().strip()
    all_edits = json.loads(_out) if _out else {}
except Exception:
    all_edits = {}

preserved = 0
for fp in local_frozen.glob('*.json'):
    name = fp.name
    mya_edits = all_edits.get(name) or {}
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

echo "  → preserving Mya-side actuals..."
python3 - <<'PYEOF'
import json, os, subprocess
from pathlib import Path
host = os.environ['MYA_SSH_HOST']
remote_base = os.environ.get('MYA_REMOTE_BASE', '/opt/vito/gepo-backtest/live')
local_path = Path('live/actuals.json')
ssh_key = os.environ.get('MYA_SSH_KEY', '')
ssh_opts = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new']
if ssh_key: ssh_opts += ['-i', ssh_key]

def empty():
    return {'version': 1, 'trades': []}

try:
    out = subprocess.check_output(['ssh'] + ssh_opts + [host,
        f"python3 -c \"import json, pathlib; p=pathlib.Path('{remote_base}/actuals.json'); print(p.read_text() if p.exists() else '{{}}')\""
    ], stderr=subprocess.DEVNULL, timeout=15).decode()
    remote = json.loads(out) if out.strip() else empty()
except Exception:
    remote = empty()

if local_path.exists():
    try:
        local = json.loads(local_path.read_text())
    except Exception:
        local = empty()
else:
    local = empty()

merged = {'version': 1, 'trades': []}
# 2026-09-15: field-aware. The mini writes marks and settlement into a trade's
# pick (track_frozen / snapshot_picks.settle for kind "live"); Mya holds the
# user's inline fill edits. "Remote wins wholesale" threw the marks away on
# every upload. Now: local trade as the base, Mya's fill fields on top.
USER_KEYS = ('actual_credit', 'actual_max_loss')
loc = {t.get('id'): t for t in (local.get('trades') or []) if t.get('id')}
rem = {t.get('id'): t for t in (remote.get('trades') or []) if t.get('id')}
by_id = {}
for tid in list(loc) + [k for k in rem if k not in loc]:
    if tid in loc and tid in rem:
        t = dict(loc[tid])
        for k in USER_KEYS:
            if k in rem[tid]:
                t[k] = rem[tid][k]
        by_id[tid] = t
    else:
        by_id[tid] = loc.get(tid) or rem.get(tid)
merged['trades'] = list(by_id.values())
updated = max([x for x in [local.get('updated_at'), remote.get('updated_at')] if x] or [''])
if updated:
    merged['updated_at'] = updated

if merged['trades'] or local_path.exists():
    tmp = local_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(merged, indent=2))
    tmp.replace(local_path)
print(f"  → preserved/merged {len(merged['trades'])} actual trade(s)")
PYEOF

# rsync flags:
#   -a : archive (preserves timestamps, perms)
#   -z : compress in transit
#   --partial : keep partial transfers (helps on flaky links)
#   --timeout=20 : bail if no progress
#   no --delete : keep server-side history files even if pruned locally
RSYNC=(rsync -az --partial --timeout=20 -e "$SSH_COMMAND")

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
  "live/ranked/actuals_marks.json"  # open-position marks off their OWN legs (mark_actuals.py)
  "live/notifications/"             # health alerts and (later) 15:45 freeze payloads
  "live/frozen/"                    # daily 15:45 freeze snapshots (History tab)
  "live/intraday_picks/"            # every :01/:31 scan's qualified picks (Snapshots tab)
  "live/actuals.json"               # user-selected actual trades (Actuals tab)
  "live/data/backtest_equity.json"  # precomputed equity curve (Backtest tab)
  "live/data/oot_equity.json"       # 2026 out-of-time equity curve (OOT tab)
)

# TODAY's snapshot parquet. The webapp prices every OPEN position's mark from it
# (_track_from_snapshot); without it Mya fell all the way through to INTRINSIC,
# which on a multi-day spread is max loss or full win and nothing in between --
# the Actuals card read -$233 on 2026-09-21 against a true -$40. Only today's
# folder is sent: history marks come from the frozen files, not from parquets.
_SNAP_DAY="live/snapshots/$(date '+%Y-%m-%d')"
if [ -d "$_SNAP_DAY" ]; then
  if $SSH_COMMAND "$MYA_SSH_HOST" "mkdir -p '$MYA_REMOTE_BASE/snapshots/$(date '+%Y-%m-%d')'" 2>/dev/null; then
    "${RSYNC[@]}" "$_SNAP_DAY/" "$MYA_SSH_HOST:$MYA_REMOTE_BASE/snapshots/$(date '+%Y-%m-%d')/" \
      && echo "  ✓ uploaded $_SNAP_DAY" \
      || echo "  ✗ rsync failed for $_SNAP_DAY"
  else
    echo "  ✗ could not create remote snapshot dir"
  fi
fi

for src in "${UPLOAD_FILES[@]}"; do
  if [ ! -e "$src" ]; then
    echo "  · skip $src (not present)"
    continue
  fi
  dest="$MYA_SSH_HOST:$MYA_REMOTE_BASE/${src#live/}"
  if [ "$src" = "live/notifications/" ]; then
    # Health alerts are event payloads. Re-syncing old health-*.json on every
    # normal data upload can make Mya re-send stale alerts outside market hours.
    "${RSYNC[@]}" --exclude 'health-*.json' "$src" "$dest" \
      && echo "  ✓ uploaded $src (excluding health alerts)" \
      || echo "  ✗ rsync failed for $src"
    continue
  fi
  "${RSYNC[@]}" "$src" "$dest" \
    && echo "  ✓ uploaded $src" \
    || echo "  ✗ rsync failed for $src"
done
