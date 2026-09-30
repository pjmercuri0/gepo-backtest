"""Relaxed Fable on vendor: no quote gate, rank by MODEL credit/width (user 2026-09-30)."""
import sys, io, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec, config as cfg, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
HERE = Path(__file__).resolve().parent
KEY = ["ticker", "entry_date", "expiry_date", "spread_type", "short_strike", "long_strike"]
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
q = pd.read_parquet(HERE / "frame_quotes.parquet")
c = c.merge(q[KEY + ["vendor_mid", "IV", "s_bid", "s_ask"]], on=KEY, how="left")
c["cw_model"] = c.model_credit / c.width; c["cw_quoted"] = c.vendor_mid / c.width
gates = ~c.exdiv_hit & ~c.earnings_hit
bull = c.spread_type.eq("bull_put") & gates & (c.cw_model >= 0.45)
bear = c.spread_type.eq("bear_call") & gates & (c.cw_model >= 0.45) & (c.IV < 0.35)
bear_reg = bear & c.below_100
# wide-quote check on the eligible pool
e = c[bull | bear]
print("eligible pool: vendor_mid/model median %.3f, p10 %.3f, p90 %.3f; short-leg bid-ask/mid median %.3f, p90 %.3f"
      % ((e.vendor_mid / e.model_credit).median(), (e.vendor_mid / e.model_credit).quantile(.1), (e.vendor_mid / e.model_credit).quantile(.9),
         ((e.s_ask - e.s_bid) / ((e.s_ask + e.s_bid) / 2)).median(), ((e.s_ask - e.s_bid) / ((e.s_ask + e.s_bid) / 2)).quantile(.9)))

def run(name, elig, key, pooled=True, top=6):
    e = c[elig].sort_values(["entry_date", key], ascending=[True, False])
    sel = e.groupby("entry_date", sort=False).head(top) if pooled else e.groupby(["entry_date", "spread_type"], sort=False).head(top)
    picks = rbr.enrich(realize(sel, 10**6)); rows = []
    for lab, m in (("IS", picks.entry_date.dt.year <= 2025), ("OOT", picks.entry_date.dt.year == 2026)):
        p = picks[m]
        with contextlib.redirect_stdout(io.StringIO()):
            s = rmc.build_payload(p, 2025 if lab == "IS" else 2026, name)["summary"]
        yrs = p.groupby(p.entry_date.dt.year).pnl_per_contract.sum()
        rows.append(dict(variant=name, window=lab, n=len(p), bulls=int(p.spread_type.eq("bull_put").sum()), win=round(100 * p._outcome.eq("WIN").mean(), 1),
                         full_loss=round(100 * p._outcome.eq("LOSS").mean(), 1), qty2_final=s["strategy_final"], dsharpe=s["strategy_sharpe_dollar"],
                         dd_qty2=s["strategy_max_dd"], yield_pct=s["strategy_yield"], neg_years=int((yrs < 0).sum())))
    return rows
out = []
out += run("model_rank_noQG_pooled6", bull | bear, "cw_model")
out += run("model_rank_noQG_3side", bull | bear, "cw_model", pooled=False, top=3)
out += run("model_rank_noQG_bulls_only", bull, "cw_model")
out += run("model_rank_noQG_bear_regime", bull | bear_reg, "cw_model")
out += run("model_rank_QG_pooled6", (bull | bear) & (c.vendor_mid >= c.model_credit), "cw_model")
out += run("GROUND_rank_noQG_pooled6", bull | bear, "GROUND")
out += run("GROUND_rank_noQG_bear_regime", bull | bear_reg, "GROUND")
df = pd.DataFrame(out); df.to_csv(HERE / "results_relaxed.csv", index=False); print(df.to_string(index=False))
