"""Variants A/B/C and the §0.67 canon with entries allowed only when the name has >= 252 sessions of
close history before entry (user 2026-09-30: 'refresh the data only with 252 history. start when you have it')."""
import sys, io, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
HERE = Path(__file__).resolve().parent; KEY = ["ticker", "entry_date", "expiry_date", "spread_type", "short_strike", "long_strike"]
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
c = c.merge(pd.read_parquet(HERE / "frame_quotes.parquet")[KEY + ["IV"]], on=KEY, how="left")
cl = pd.read_parquet(ROOT / "output/daily_closes.parquet"); cl["date"] = pd.to_datetime(cl.date).dt.normalize()
cl = cl.dropna(subset=["close"]).sort_values(["ticker", "date"]); cl["n_before"] = cl.groupby("ticker").cumcount()   # sessions strictly before this date
c = c.merge(cl[["ticker", "date", "n_before"]].rename(columns={"date": "entry_date"}), on=["ticker", "entry_date"], how="left")
n0 = len(c); c = c[c.n_before >= ec.WINDOW].copy()
print(f"252-history gate: {n0:,} -> {len(c):,} candidates; first entry {c.entry_date.min().date()} (was 2020-08-03)")
g = ~c.exdiv_hit & ~c.earnings_hit; cw = c.model_credit / c.width
bull = c.spread_type.eq("bull_put") & g & (cw >= 0.45)
bear_reg = c.spread_type.eq("bear_call") & g & (cw >= 0.45) & (c.IV < 0.35) & c.below_100
bear_all = c.spread_type.eq("bear_call") & g & (cw >= 0.45) & (c.IV < 0.35)
c["cw_model"] = cw
def run(name, elig, key, pooled=True, top=6, canon=False):
    if canon:
        bp = c[c.spread_type.eq("bull_put") & g & (c.GROUND >= ec.THR)]; brp = c[c.spread_type.eq("bear_call") & g & c.below_100 & (c.GROUND >= rbr.GROUND)]
        picks = rbr.enrich(pd.concat([realize(bp, ec.TOP_N), realize(brp, rbr.CAP)], ignore_index=True))
    else:
        e = c[elig].sort_values(["entry_date", key], ascending=[True, False])
        picks = rbr.enrich(realize(e.groupby("entry_date", sort=False).head(top), 10**6))
    rows = []
    for lab, m in (("IS", picks.entry_date.dt.year <= 2025), ("OOT", picks.entry_date.dt.year == 2026)):
        p = picks[m]
        with contextlib.redirect_stdout(io.StringIO()):
            s = rmc.build_payload(p, 2025 if lab == "IS" else 2026, name)["summary"]
        yrs = p.groupby(p.entry_date.dt.year).pnl_per_contract.sum()
        rows.append(dict(variant=name, window=lab, start=p.entry_date.min().date(), n=len(p), bulls=int(p.spread_type.eq("bull_put").sum()),
                         win=round(100 * p._outcome.eq("WIN").mean(), 1), full_loss=round(100 * p._outcome.eq("LOSS").mean(), 1),
                         qty2_final=s["strategy_final"], dsharpe=s["strategy_sharpe_dollar"], dd_qty2=s["strategy_max_dd"], dd_qty1=s["qty1_max_dd"],
                         yield_pct=s["strategy_yield"], neg_years=int((yrs < 0).sum())))
    return rows
out = []
out += run("A model-rank, bears daily", bull | bear_all, "cw_model")
out += run("B model-rank, bears below 100d", bull | bear_reg, "cw_model")
out += run("C GROUND-rank, bears below 100d", bull | bear_reg, "GROUND")
out += run("canon 0.67 (same 252 gate)", None, None, canon=True)
df = pd.DataFrame(out); df.to_csv(HERE / "results_252.csv", index=False); print(df.to_string(index=False))
