"""k sweep for strategy C: GROUND = EV * exp(-k D_ent) as the pooled top-6 rank key (252 gate, no quote gate)."""
import sys, io, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
HERE = Path(__file__).resolve().parent; KEY = ["ticker", "entry_date", "expiry_date", "spread_type", "short_strike", "long_strike"]
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
c = c.merge(pd.read_parquet(HERE / "frame_quotes.parquet")[KEY + ["IV"]], on=KEY)
cl = pd.read_parquet(ROOT / "output/daily_closes.parquet"); cl["date"] = pd.to_datetime(cl.date).dt.normalize()
cl = cl.dropna(subset=["close"]).sort_values(["ticker", "date"]); cl["n_before"] = cl.groupby("ticker").cumcount()
c = c.merge(cl[["ticker", "date", "n_before"]].rename(columns={"date": "entry_date"}), on=["ticker", "entry_date"]); c = c[c.n_before >= 252]
g = ~c.exdiv_hit & ~c.earnings_hit; cw = c.model_credit / c.width
pool = c[(c.spread_type.eq("bull_put") & g & (cw >= 0.45)) | (c.spread_type.eq("bear_call") & g & (cw >= 0.45) & (c.IV < 0.35) & c.below_100)].copy()
base = None; out = []
KS = [float(x) for x in sys.argv[1:]] or [0, 1, 2, 3, 4, 5, 6, 8, 12, 16, 24]
OUTCSV = HERE / ("k_sweep_C_ext.csv" if sys.argv[1:] else "k_sweep_C.csv")
for k in KS:
    pool["key"] = pool.EV * np.exp(-k * pool.D_ent)
    sel = pool.sort_values(["entry_date", "key"], ascending=[True, False]).groupby("entry_date", sort=False).head(6)
    picks = rbr.enrich(realize(sel, 10**6)); ids = set(map(tuple, picks[KEY].astype(str).values))
    if base is None: base = ids
    row = dict(k=k, overlap_k4=None)
    for lab, m, yr in (("IS", picks.entry_date.dt.year <= 2025, 2025), ("OOT", picks.entry_date.dt.year == 2026, 2026)):
        p = picks[m]
        with contextlib.redirect_stdout(io.StringIO()):
            s = rmc.build_payload(p, yr, "k")["summary"]
        row[f"{lab}_final"] = s["strategy_final"]; row[f"{lab}_dsh"] = s["strategy_sharpe_dollar"]; row[f"{lab}_dd"] = s["strategy_max_dd"]; row[f"{lab}_yield"] = s["strategy_yield"]
        row[f"{lab}_fullloss"] = round(100 * p._outcome.eq("LOSS").mean(), 1)
    out.append((row, ids))
k4 = [ids for r, ids in out if r["k"] == 4][0]
rows = []
for r, ids in out:
    r["overlap_k4"] = round(100 * len(ids & k4) / len(k4), 1); rows.append(r)
df = pd.DataFrame(rows); df.to_csv(OUTCSV, index=False); print(df.to_string(index=False))
