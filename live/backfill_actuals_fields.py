"""Backfill fields that snapshot-sourced Actuals trades were never given.

live/snapshot_picks.py wrote a 14-field subset of each pick, so a trade copied
into Actuals from the Snapshots tab arrived with no short_delta, IV, bid/ask or
open interest — the keys were absent, not null, so the page showed "-" forever.
snapshot_picks.py now keeps those fields, but trades added before that change
cannot recover them from intraday_picks/, because the values were never there.

They ARE recoverable from live/ranked/<date>_<hhmm>.json, which archives the
full ranked row for every scan, and each Actuals trade records the exact
source date and hhmm it was copied from. Falls back to that day's frozen file.

Never invents a value: a field is filled only from the scan the trade was
actually taken from (or that day's freeze), and only when currently missing.
Writes a timestamped backup of actuals.json before touching it.

Usage:  python3 -m live.backfill_actuals_fields [--apply]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live import live_config

FILL = ["short_delta", "long_delta", "IV", "long_IV",
        "short_bid", "short_ask", "long_bid", "long_ask",
        "short_oi", "long_oi"]


def _key(p: dict):
    try:
        return (p["ticker"], p["spread_type"], str(p["expiry_date"])[:10],
                round(float(p["short_strike"]), 4), round(float(p["long_strike"]), 4))
    except (KeyError, TypeError, ValueError):
        return None


def _candidates(src: dict) -> list[Path]:
    """Scan files to search, most authoritative first."""
    root = Path(live_config.ROOT_DIR)
    day, hhmm = src.get("date"), src.get("hhmm")
    out = []
    if day and hhmm:
        out.append(root / "ranked" / f"{day}_{hhmm}.json")      # the exact scan
    if day:
        # Same day, NEAREST scan first. Delta drifts through the session, so a
        # pick taken at 10:32 should not be backfilled from the 09:31 scan just
        # because that file sorts first.
        def _dist(fp: Path) -> int:
            try:
                t = fp.stem.split("_")[1]
                return abs((int(t[:2]) * 60 + int(t[2:]))
                           - (int(hhmm[:2]) * 60 + int(hhmm[2:]))) if hhmm else 0
            except (IndexError, ValueError):
                return 10 ** 6
        out += sorted((root / "ranked").glob(f"{day}_*.json"), key=_dist)
        out.append(Path(live_config.FROZEN_DIR) / f"{day}.json")
    return [p for p in out if p.exists()]


def _rows(path: Path) -> list[dict]:
    try:
        d = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    for k in ("top_picks", "picks", "ticker"):
        v = d.get(k)
        if isinstance(v, list):
            return [r for r in v if isinstance(r, dict)]
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the changes; default is a dry run")
    args = ap.parse_args()

    store_path = Path(live_config.ROOT_DIR) / "actuals.json"
    store = json.loads(store_path.read_text())
    filled = missing = 0

    for t in store.get("trades") or []:
        pick = t.get("pick") or {}
        want = [f for f in FILL if pick.get(f) is None]
        if not want:
            continue
        k = _key(pick)
        if k is None:
            continue
        hit = None
        for path in _candidates(t.get("source") or {}):
            for r in _rows(path):
                if _key(r) == k and r.get("short_delta") is not None:
                    hit = (path, r)
                    break
            if hit:
                break
        if not hit:
            missing += 1
            print(f"  {pick.get('ticker'):<6} no source scan found — leaving as is")
            continue
        path, r = hit
        got = [f for f in want if r.get(f) is not None]
        for f in got:
            pick[f] = r[f]
        filled += 1
        src_hhmm = (t.get("source") or {}).get("hhmm") or "?"
        print(f"  {pick.get('ticker'):<6} src {src_hhmm}  <- {path.name:<26} "
              f"delta={r.get('short_delta')}  filled {len(got)}/{len(want)}")

    print(f"\n  {filled} trade(s) backfilled, {missing} unresolved")
    if not args.apply:
        print("  DRY RUN — nothing written. Re-run with --apply.")
        return 0
    bak = store_path.with_name(
        f"actuals.backup-{datetime.now():%Y%m%d-%H%M%S}.json")
    shutil.copy2(store_path, bak)
    store_path.write_text(json.dumps(store, indent=1))
    print(f"  backup: {bak.name}\n  wrote {store_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
