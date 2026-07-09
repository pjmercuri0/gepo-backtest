"""Drift today's frozen picks to fresh metrics.

Reads `live/frozen/<today>.json` (the 5 picks selected at 15:45), re-runs
the canonical ranking pipeline on today's latest snapshot parquet, then —
for each frozen pick — looks it up in the re-ranked frame by identity
(ticker + spread_type + short_strike + long_strike + expiry_date) and
overwrites its entry-time metric fields with the fresh values.

Identity fields stay locked. Metric fields (entry_price, net_credit,
short_delta, GROUND, etc.) are replaced. Sets `drift_at` to the time
string passed via --drift-at (default "16:00").

Picks not found in the re-ranked frame (regime/OI gate, options dropped
from the chain) keep their 15:45 metrics — the script logs a warning.

Usage:
  python3 -m live.drift_frozen                       # default drift-at "16:00"
  python3 -m live.drift_frozen --drift-at 16:00      # explicit
  python3 -m live.drift_frozen --snapshot PATH       # specific parquet
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live import live_config
from live.ranker import rank_snapshot, _latest_snapshot


# Fields that get overwritten with fresh metrics. Identity fields
# (ticker, spread_type, short_strike, long_strike, expiry_date) and
# `entry_date` (15:45 conceptual entry) are intentionally omitted.
METRIC_FIELDS = (
    "entry_price",
    "net_credit",
    "spread_width",
    "max_loss",
    "short_delta",
    "long_delta",
    "credit_ratio",
    "IV",
    "DTE",
    "p", "q", "ro",
    "G", "EV", "DKL",
    "GROUND",
    "w_star",
    "qualified",
)


def _num(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return float(v)


def _ratio(num, denom):
    if num is None or denom is None or denom == 0:
        return None
    return num / denom


def _find_match(ranked: pd.DataFrame, pick: dict) -> pd.Series | None:
    """Locate the pick's row in the re-ranked frame by identity match."""
    exp = pd.Timestamp(pick["expiry_date"])
    rows = ranked[
        (ranked["ticker"] == pick["ticker"]) &
        (ranked["spread_type"] == pick["spread_type"]) &
        (ranked["short_strike"].sub(float(pick["short_strike"])).abs() < 1e-6) &
        (ranked["long_strike"].sub(float(pick["long_strike"])).abs() < 1e-6) &
        (pd.to_datetime(ranked["expiry_date"]) == exp)
    ]
    if rows.empty:
        return None
    return rows.iloc[0]


def _atomic_write(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def drift_pick(pick: dict, row: pd.Series) -> None:
    """Mutate `pick` in place, replacing metric fields from `row`.

    Preserves the original 15:45 values in `pick['metrics_at_freeze']` on
    the first drift. Re-running drift is idempotent — the snapshot stays
    locked to the 15:45 values, not the most recently-drifted ones.
    """
    if "metrics_at_freeze" not in pick:
        pick["metrics_at_freeze"] = {k: pick.get(k) for k in METRIC_FIELDS}
    for k in METRIC_FIELDS:
        if k == "credit_ratio":
            pick[k] = _num(_ratio(row.get("net_credit"), row.get("max_loss")))
        elif k == "DTE":
            v = row.get("DTE")
            try:
                pick[k] = int(v) if v is not None and not pd.isna(v) else None
            except (TypeError, ValueError):
                pick[k] = None
        elif k == "qualified":
            pick[k] = bool(row.get("qualified", True))
        else:
            pick[k] = _num(row.get(k))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drift-at", default="16:00",
                        help="Label written to frozen file's drift_at field")
    parser.add_argument("--snapshot", default=None,
                        help="Specific parquet (default: today's most recent)")
    parser.add_argument("--frozen", default=None,
                        help="Specific frozen JSON path (default: today's)")
    args = parser.parse_args()

    today = date.today()
    frozen_path = (Path(args.frozen).resolve() if args.frozen
                   else Path(live_config.FROZEN_DIR) / f"{today.isoformat()}.json")
    if not frozen_path.exists():
        print(f"No frozen file for today ({today}); nothing to drift.", flush=True)
        return 0

    with open(frozen_path) as f:
        payload = json.load(f)

    if payload.get("mock"):
        print(f"Frozen file is MOCK; skipping drift.", flush=True)
        return 0

    picks = payload.get("top_picks") or []
    if not picks:
        print(f"Frozen file has no top_picks; skipping drift.", flush=True)
        return 0

    snap_path = Path(args.snapshot).resolve() if args.snapshot else _latest_snapshot()
    if snap_path is None or not snap_path.exists():
        print("No snapshot parquet available; skipping drift.", flush=True)
        return 1

    print(f"Drifting {frozen_path.name} against {snap_path}", flush=True)
    df = pd.read_parquet(snap_path)
    print(f"  {len(df)} option rows loaded", flush=True)

    ranked = rank_snapshot(df)
    print(f"  {len(ranked)} ranked candidates after filters + GROUND", flush=True)

    drifted = 0
    missing: list[str] = []
    for pick in picks:
        match = _find_match(ranked, pick) if not ranked.empty else None
        if match is None:
            missing.append(pick["ticker"])
            continue
        drift_pick(pick, match)
        drifted += 1
        print(f"    [{pick['ticker']}] drifted: "
              f"credit=${pick['net_credit']:.2f} "
              f"GROUND={pick['GROUND']*100:.3f}% "
              f"δshort={pick['short_delta']:.2f}", flush=True)

    payload["drift_at"] = args.drift_at
    payload["drift_ts"] = datetime.now().isoformat(timespec="seconds")
    payload["drift_snapshot_file"] = str(snap_path.relative_to(ROOT))

    if missing:
        print(f"  ! {len(missing)} pick(s) not found in re-ranked frame: "
              f"{', '.join(missing)} — kept 15:45 metrics.", flush=True)
        payload["drift_missing"] = missing

    _atomic_write(frozen_path, payload)
    print(f"✓ drifted {drifted}/{len(picks)} picks; wrote {frozen_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
