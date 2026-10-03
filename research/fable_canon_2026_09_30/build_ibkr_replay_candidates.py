"""Rebuild the IBKR fair-replay candidate frame from the archived snapshots (mini, 2026-10-02).

Every live/snapshots/<day>/{1530,1531,1545}.parquet is re-ranked with live.ranker.rank_snapshot as the
code stands now (LIVE_COMBO_ENABLED = False: quotes = snapshot leg mids, no IB calls). settle = the
expiry-day close from live.closes.load_closes(); rows whose expiry has no close yet are dropped.
Columns match ibkr_replay_candidates_1530_1545.csv (README_ibkr_replay.md); G24 = EV * exp(-24 D_ent).
Usage: build_ibkr_replay_candidates.py [since=2026-08-20] [out=ibkr_replay_candidates_1530_1545.csv]
"""
import sys, io, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
import os; os.chdir(ROOT)
from live import live_config, ranker
from live.closes import load_closes
HERE = Path(__file__).resolve().parent
SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-08-20"
OUT = HERE / (sys.argv[2] if len(sys.argv) > 2 else "ibkr_replay_candidates_1530_1545.csv")
live_config.LIVE_COMBO_ENABLED = False

closes = load_closes(); closes["date"] = pd.to_datetime(closes.date).dt.strftime("%Y-%m-%d")
px = closes.set_index(["ticker", "date"]).close
rows = []
for dd in sorted(p for p in (ROOT / "live/snapshots").iterdir() if p.is_dir() and p.name >= SINCE):
    for hm in ("1530", "1531", "1545"):
        f = dd / f"{hm}.parquet"
        if not f.exists():
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            r = ranker.rank_snapshot(pd.read_parquet(f))
        if r is None or r.empty:
            print(dd.name, hm, "no candidates"); continue
        r = r.copy(); r["day"] = dd.name; r["hm"] = hm; rows.append(r)
        print(dd.name, hm, len(r), flush=True)
R = pd.concat(rows, ignore_index=True)
R["exp"] = pd.to_datetime(R.expiry_date).dt.strftime("%Y-%m-%d")
R["width"] = (R.short_strike.astype(float) - R.long_strike.astype(float)).abs()
R["model_cw"] = R.model_credit / R.width
R["quote_over_model"] = R.net_credit / R.model_credit
R["G24"] = R.EV * np.exp(-24.0 * R.D_ent)
R["settle"] = [px.get((t, e), np.nan) for t, e in zip(R.ticker, R.exp)]
cols = ["day", "hm", "ticker", "spread_type", "short_strike", "long_strike", "width", "exp", "DTE", "entry_price",
        "net_credit", "model_credit", "model_cw", "quote_over_model", "IV", "p", "q", "ro", "G", "EV", "D_ent", "GROUND", "G24", "settle"]
R = R[cols]
print(f"{len(R)} rows, {R.day.nunique()} days, {R.settle.isna().sum()} unsettled (dropped), exp max {R.exp.max()}")
R[R.settle.notna()].to_csv(OUT, index=False)
print("wrote", OUT)
