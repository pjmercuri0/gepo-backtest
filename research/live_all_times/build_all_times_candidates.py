"""Every in-hours live IBKR snapshot since SINCE, re-ranked with live.ranker.rank_snapshot as the code
stands now (LIVE_COMBO_ENABLED = False: quotes = snapshot leg mids, no IB calls), settled at the
expiry-day close. The all-times sibling of fable_canon_2026_09_30/build_ibkr_replay_candidates.py,
which keeps only 15:30/15:45. Rows whose expiry has no close yet are dropped.
Usage: build_all_times_candidates.py [since=2026-08-08]   ->  all_times_candidates.csv
"""
import sys, io, os, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
from multiprocessing import Pool
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
HERE = Path(__file__).resolve().parent
SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-08-08"
KEEP = ["ticker", "spread_type", "short_strike", "long_strike", "expiry_date", "DTE", "entry_price", "short_delta",
        "net_credit", "model_credit", "IV", "rv_30d", "p", "q", "ro", "G", "EV", "D_ent", "GROUND", "above_min", "qualified",
        "cw_floor", "short_oi", "long_oi", "short_bid", "short_ask", "long_bid", "long_ask"]

def one(f):
    from live import live_config, ranker
    live_config.LIVE_COMBO_ENABLED = False
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            r = ranker.rank_snapshot(pd.read_parquet(f))
    except Exception as e:
        return f"{f.parent.name} {f.stem} ERROR {type(e).__name__}: {e}"
    if r is None or r.empty:
        return None
    r = r[[c for c in KEEP if c in r.columns]].copy(); r["day"] = f.parent.name; r["hm"] = f.stem
    return r

if __name__ == "__main__":
    files = [f for dd in sorted(p for p in (ROOT / "live/snapshots").iterdir() if p.is_dir() and p.name >= SINCE)
             for f in sorted(dd.glob("[0-9][0-9][0-9][0-9].parquet")) if "0930" <= f.stem < "1600"]
    with Pool(6) as pool:
        res = pool.map(one, files, chunksize=4)
    errs = [x for x in res if isinstance(x, str)]; frames = [x for x in res if isinstance(x, pd.DataFrame)]
    print(f"{len(files)} snapshots, {len(frames)} with candidates, {len(errs)} errors"); [print(" ", e) for e in errs[:20]]
    R = pd.concat(frames, ignore_index=True)
    from live.closes import load_closes
    closes = load_closes(); closes["date"] = pd.to_datetime(closes.date).dt.strftime("%Y-%m-%d")
    px = closes.set_index(["ticker", "date"]).close
    R["exp"] = pd.to_datetime(R.expiry_date).dt.strftime("%Y-%m-%d")
    R["width"] = (R.short_strike.astype(float) - R.long_strike.astype(float)).abs()
    R["model_cw"] = R.model_credit / R.width; R["quote_cw"] = R.net_credit / R.width
    R["quote_over_model"] = R.net_credit / R.model_credit
    R["G24"] = R.EV * np.exp(-24.0 * R.D_ent)
    R["settle"] = [px.get((t, e), np.nan) for t, e in zip(R.ticker, R.exp)]
    print(f"{len(R)} rows, {R.day.nunique()} days {R.day.min()}..{R.day.max()}, {int(R.settle.isna().sum())} unsettled (dropped)")
    R[R.settle.notna()].drop(columns=["expiry_date"]).to_csv(HERE / "all_times_candidates.csv", index=False)
    print("wrote", HERE / "all_times_candidates.csv")
