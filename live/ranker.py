"""Apply canonical filters + GROUND scoring to the latest live snapshot.

Reads the most recent snapshot from live/snapshots/, calls the *same*
spreads.build_candidates() and ground.compute_ground() the backtest uses,
applies the regime gate, then writes a ranked top-N to
live/ranked/latest.json (atomic) and an archival copy to
live/ranked/<timestamp>.json.

Also enforces the MIN_OPEN_INTEREST gate, which the backtest CSVs did not
need (preprocessed historical data was already liquidity-filtered upstream).

CLI:
  python -m live.ranker                   # rank latest snapshot
  python -m live.ranker --snapshot path/to/HHMM.parquet  # rank specific file
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as backtest_config
import spreads
import ground
from live import live_config
from live.regime import current_regime


# ── Snapshot discovery ──────────────────────────────────────────────────────

def _latest_snapshot() -> Path | None:
    base = Path(live_config.SNAPSHOTS_DIR)
    if not base.exists():
        return None
    days = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
    for d in days:
        files = sorted(d.glob("*.parquet"), reverse=True)
        if files:
            return files[0]
    return None


# ── Ranking pipeline ────────────────────────────────────────────────────────

def rank_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """Run the full backtest-canonical ranking pipeline on a live snapshot."""
    if df.empty:
        return pd.DataFrame()

    # Liquidity gate (canonical: MIN_OPEN_INTEREST = 10). Applied here because
    # historical CSVs were already filtered; live data is not.
    if "OpenInterest" in df.columns:
        before = len(df)
        df = df[df["OpenInterest"] >= backtest_config.MIN_OPEN_INTEREST]
        print(f"  OI gate {backtest_config.MIN_OPEN_INTEREST}: kept {len(df)}/{before} rows",
              flush=True)
    if df.empty:
        return pd.DataFrame()

    # Regime lookup (SPY 100d SMA). Refresh from disk on every rank — fast.
    spy_csv = os.path.join(backtest_config.DATA_DIR, "spy_us_d.csv")
    spreads.REGIME_LOOKUP     = spreads.build_regime_lookup(
        spy_csv, sma_window=backtest_config.REGIME_WINDOW
    )
    spreads.REGIME_FILTER     = backtest_config.REGIME_FILTER
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER        = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS    = 0.0

    # Build candidates (one per ticker × direction × expiry).
    candidates = spreads.build_candidates(df)
    print(f"  built {len(candidates)} candidate spreads", flush=True)
    if candidates.empty:
        return pd.DataFrame()

    # Score with GROUND. ground.compute_ground returns a per-row Series of
    # {p, q, ro, w_star, G, DKL, GROUND, ...}. We apply it row-wise.
    score_cols = candidates.apply(
        lambda row: ground.compute_ground(row), axis=1
    )
    ranked = pd.concat([candidates, score_cols], axis=1)

    # Drop rows where GROUND couldn't be computed (e.g. extreme probabilities).
    ranked = ranked.dropna(subset=["GROUND"])

    # Threshold filter (canonical: GROUND_THRESHOLD = 0.0).
    ranked = ranked[ranked["GROUND"] >= backtest_config.GROUND_THRESHOLD]

    # Sort by GROUND descending.
    ranked = ranked.sort_values("GROUND", ascending=False).reset_index(drop=True)
    return ranked


# ── Output serialization ────────────────────────────────────────────────────

def _serialize(ranked: pd.DataFrame, snapshot_path: Path) -> dict:
    """Build the JSON payload consumed by the webapp."""
    snap_time = datetime.now()
    # Top-N picks (canonical)
    top = ranked.head(live_config.TOP_N_DISPLAY)
    ticker_rows = ranked.head(live_config.TICKER_LIMIT)

    def row_to_dict(r):
        # JSON-safe rendering of one ranked spread.
        return {
            "ticker":           r.get("ticker"),
            "spread_type":      r.get("spread_type"),
            "entry_date":       _isodate(r.get("entry_date")),
            "expiry_date":      _isodate(r.get("expiry_date")),
            "entry_price":      _num(r.get("entry_price")),
            "short_strike":     _num(r.get("short_strike")),
            "long_strike":      _num(r.get("long_strike")),
            "short_delta":      _num(r.get("short_delta")),
            "long_delta":       _num(r.get("long_delta")),
            "net_credit":       _num(r.get("net_credit")),
            "spread_width":     _num(r.get("spread_width")),
            "max_loss":         _num(r.get("max_loss")),
            "credit_ratio":     _num(_ratio(r.get("net_credit"), r.get("max_loss"))),
            "IV":               _num(r.get("IV")),
            "DTE":              _int(r.get("DTE")),
            "p":                _num(r.get("p")),
            "q":                _num(r.get("q")),
            "ro":               _num(r.get("ro")),
            "G":                _num(r.get("G")),
            "DKL":              _num(r.get("DKL")),
            "GROUND":           _num(r.get("GROUND")),
            "w_star":           _num(r.get("w_star")),
        }

    return {
        "snapshot_ts":   snap_time.isoformat(timespec="seconds"),
        "snapshot_file": str(snapshot_path.relative_to(ROOT)),
        "data_date":     snap_time.date().isoformat(),
        "n_candidates":  int(len(ranked)),
        "config": {
            "DTE_MIN":          backtest_config.DTE_MIN,
            "DTE_MAX":          backtest_config.DTE_MAX,
            "DELTA_MIN":        backtest_config.DELTA_MIN,
            "DELTA_MAX":        backtest_config.DELTA_MAX,
            "MIN_CREDIT_RATIO": backtest_config.MIN_CREDIT_RATIO,
            "MIN_OPEN_INTEREST": backtest_config.MIN_OPEN_INTEREST,
            "MAX_MAX_LOSS":     backtest_config.MAX_MAX_LOSS,
            "GROUND_THRESHOLD": backtest_config.GROUND_THRESHOLD,
            "TOP_N":            live_config.TOP_N_DISPLAY,
            "DKL_K":            getattr(ground, "DKL_K", 20.0),
            "ALPHA":            backtest_config.ALPHA,
        },
        "regime":    current_regime(),
        "top_picks": [row_to_dict(r) for _, r in top.iterrows()],
        "ticker":    [row_to_dict(r) for _, r in ticker_rows.iterrows()],
    }


def _num(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return float(v)


def _int(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return int(v)


def _isodate(v):
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat() if hasattr(v, "year") else v.date().isoformat()
    return str(v)


def _ratio(num, denom):
    if num is None or denom is None or denom == 0:
        return None
    return num / denom


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to a temp file in the same dir, then rename.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=None, help="Specific snapshot parquet to rank")
    args = parser.parse_args()

    if args.snapshot:
        snap_path = Path(args.snapshot).resolve()
    else:
        latest = _latest_snapshot()
        if latest is None:
            print("no snapshots found in live/snapshots/", flush=True)
            return 1
        snap_path = latest

    print(f"ranking snapshot: {snap_path}", flush=True)
    df = pd.read_parquet(snap_path)
    print(f"  {len(df)} option rows loaded", flush=True)

    ranked = rank_snapshot(df)
    print(f"  {len(ranked)} ranked candidates after filters + GROUND", flush=True)

    if ranked.empty:
        print("nothing ranked; writing empty payload anyway", flush=True)

    payload = _serialize(ranked, snap_path)

    latest_path = Path(live_config.RANKED_DIR) / "latest.json"
    archive_path = Path(live_config.RANKED_DIR) / f"{snap_path.parent.name}_{snap_path.stem}.json"
    _atomic_write_json(latest_path, payload)
    _atomic_write_json(archive_path, payload)
    print(f"wrote {latest_path}", flush=True)
    print(f"wrote {archive_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
