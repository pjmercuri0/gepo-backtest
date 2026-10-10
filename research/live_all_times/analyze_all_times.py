"""What drives win% and P&L on the live all-times frame (all_times_candidates.csv): entry time, or
credit/width, GROUND, DTE, IV ...?  Population = canon-eligible bull puts (model credit >= 0.45 x width,
quote >= model at 2dp), booked 1.00 x model, settled with spreads.settle_pnl's rule.
The same spread repeats across the scans of a day and every name-expiry shares one settlement, so
inference is clustered on ticker x expiry and repeated with expiry-week fixed effects.
Needs statsmodels (not in the project venv).  Usage: analyze_all_times.py
"""
import sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd, statsmodels.formula.api as smf
HERE = Path(__file__).resolve().parent
R = pd.read_csv(HERE / "all_times_candidates.csv", dtype={"hm": str})
E = R[(R.model_cw >= 0.45) & R.above_min.astype(bool) & (R.model_credit > 0)].copy()
E["credit"] = E.model_credit.clip(upper=E.width - 0.01)
raw = E.credit - (E.short_strike - E.settle).clip(lower=0, upper=E.width)
part = (E.settle <= E.short_strike) & (E.settle > E.long_strike)
E["pnl"] = np.where(part & (raw > 0), raw * 0.5, raw) * 100
E["ror"] = E.pnl / ((E.width - E.credit) * 100)            # return on risk
E["win"] = (E.settle >= E.short_strike).astype(int)
E["pos"] = (E.pnl > 0).astype(int)                           # win or positive partial
mins = E.hm.str[:2].astype(int) * 60 + E.hm.str[2:].astype(int)
E["slot"] = ((mins // 30) * 30).map(lambda m: f"{m//60:02d}:{m%60:02d}")
E["hours"] = (mins - 570) / 60.0
E["dow"] = pd.to_datetime(E.day).dt.day_name().str[:3]
E["otm"] = (E.entry_price - E.short_strike) / E.entry_price * 100    # % spot above short strike
E["gbp"] = E.G24 * 1e4
E["gband"] = pd.cut(E.G24, [-np.inf, 0, 1e-4, np.inf], labels=["<=0", "0-1bp", ">=1bp"])
E["cl"] = E.ticker + "|" + E.exp
print(f"eligible rows {len(E):,}; spread-days {E.drop_duplicates(['day','ticker','short_strike','long_strike','exp']).shape[0]:,}; "
      f"name-expiry clusters {E.cl.nunique()}; expiries {E.exp.nunique()}; days {E.day.nunique()} ({E.day.min()}..{E.day.max()})")
print(f"overall: win {E.win.mean()*100:.1f}%  win+pos partial {E.pos.mean()*100:.1f}%  $/trade {E.pnl.mean():+.2f}  return on risk {E.ror.mean()*100:+.2f}%")

def tab(col, order=None):
    g = E.groupby(col, observed=True).agg(n=("win", "size"), win=("win", "mean"), pos=("pos", "mean"), pnl=("pnl", "mean"), ror=("ror", "mean"))
    if order: g = g.reindex(order)
    g["win"] = (g.win * 100).round(1); g["pos"] = (g.pos * 100).round(1); g["pnl"] = g.pnl.round(2); g["ror"] = (g.ror * 100).round(2)
    print(f"\n-- by {col}"); print(g.to_string())
tab("slot"); tab("dow", ["Mon", "Tue", "Wed", "Thu", "Fri"]); tab("DTE"); tab("gband"); tab("exp")
E["cw_q"] = pd.qcut(E.model_cw, 5, duplicates="drop"); tab("cw_q")
E["iv_q"] = pd.qcut(E.IV, 5, duplicates="drop"); tab("iv_q")
E["otm_q"] = pd.qcut(E.otm, 5, duplicates="drop"); tab("otm_q")

# ---- how much of the outcome each factor explains: alone, and added last to all the others
FACT = {"entry time (30-min slot)": "C(slot)", "day of week / DTE": "C(dow)", "credit / width": "model_cw",
        "GROUND (bp)": "gbp", "IV": "IV", "D_ent": "D_ent", "P_real win prob": "p", "quote / model": "quote_over_model",
        "short strike % OTM": "otm", "short delta": "short_delta"}
def r2(y, terms):
    return smf.ols(f"{y} ~ " + (" + ".join(terms) if terms else "1"), E).fit().rsquared
def wald(y, term, others, fe):
    rhs = [term] + others + (["C(exp)"] if fe else [])
    m = smf.ols(f"{y} ~ " + " + ".join(rhs), E).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(E.cl)[0]})
    names = [n for n in m.params.index if n.startswith(term) or n == term]
    return float(m.wald_test(", ".join(f"{n} = 0" for n in [x.replace("[", "[").replace("]", "]") for x in names]) if False else np.eye(len(m.params))[[list(m.params.index).index(n) for n in names]], scalar=True).pvalue)
for y, lab in (("win", "WIN (0/1)"), ("ror", "P&L (return on risk)")):
    for fe in (False, True):
        base = ["C(exp)"] if fe else []
        full = list(FACT.values()) + base
        r_full, r_base = r2(y, full), r2(y, base)
        print(f"\n== {lab}{'  | within expiry week (week fixed effects)' if fe else ''}:  R2 all factors {r_full*100:.2f}%" + (f"  (weeks alone {r_base*100:.2f}%)" if fe else ""))
        rows = []
        for name, t in FACT.items():
            alone = r2(y, [t] + base) - r_base
            last = r_full - r2(y, [x for x in full if x != t])
            rows.append((name, alone * 100, last * 100, wald(y, t, [x for x in FACT.values() if x != t], fe)))
        out = pd.DataFrame(rows, columns=["factor", "R2 alone %", "R2 added last %", "p (clustered)"]).sort_values("R2 added last %", ascending=False)
        print(out.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

# ---- standardized effects (1 SD move), clustered, with week fixed effects
Z = E.copy(); num = ["hours", "model_cw", "gbp", "IV", "D_ent", "p", "quote_over_model", "otm", "short_delta", "DTE"]
for c in num: Z[c] = (Z[c] - Z[c].mean()) / Z[c].std()
for y in ("win", "ror"):
    m = smf.ols(f"{y} ~ " + " + ".join(num) + " + C(exp)", Z).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(Z.cl)[0]})
    t = pd.DataFrame({"per 1 SD": m.params[num] * 100, "t": m.tvalues[num], "p": m.pvalues[num]}).sort_values("t", key=abs, ascending=False)
    print(f"\n== {y}: effect of a 1-SD move, pct points, week FE, clustered on name x expiry"); print(t.round(3).to_string())
print("\ncorr:"); print(E[["hours", "model_cw", "gbp", "IV", "D_ent", "p", "otm", "short_delta", "DTE"]].corr().round(2).to_string())
