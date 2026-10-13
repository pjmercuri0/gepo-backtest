"""Does a name's prevalence over the day predict the 15:xx picks? (user 2026-10-13)
Prevalence of a pick = share of that day's EARLIER scans in which the same name had a spread clearing every
canon gate (0.45 credit floor, quote gate, GROUND floor) -- `share_q`; and the share in which it sat in the
top 6 by GROUND -- `share_t`. No lookahead: only scans before the pick's own scan count.
Populations: (A) every canon-eligible spread at a 15:15-15:45 scan; (B) the canon picks (15:30 + 15:45
top-up, the Plot rule, reconciled in sim_canon_vs_rule.py). 1 contract, 1.00 x model, settle at expiry."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
import config as cfg
HERE = Path(__file__).resolve().parent; GMIN = float(cfg.FABLE_GROUND_MIN); TOP = 6
R = pd.read_csv(HERE / "all_times_candidates.csv", dtype={"hm": str})
R = R[pd.to_datetime(R.day).dt.dayofweek < 4]
E = R[(R.spread_type == "bull_put") & (R.model_cw >= 0.45) & R.above_min.astype(bool) & (R.model_credit > 0) & (R.G24 >= GMIN)].copy()
E["rk"] = E.groupby(["day", "hm"]).G24.rank(ascending=False, method="first")
scans = R.groupby("day").hm.apply(lambda s: sorted(s.unique())).to_dict()
qual = {k: set(g.ticker) for k, g in E.groupby(["day", "hm"])}
top = {k: set(g.ticker) for k, g in E[E.rk <= TOP].groupby(["day", "hm"])}
def prev(day, hm, tk):
    prior = [h for h in scans[day] if h < hm]
    if not prior: return np.nan, np.nan, 0, 0
    nq = sum(tk in qual.get((day, h), ()) for h in prior); nt = sum(tk in top.get((day, h), ()) for h in prior)
    return nq / len(prior), nt / len(prior), nq, len(prior)
def book(P):
    P = P.copy(); P["credit"] = P.model_credit.clip(upper=P.width - 0.01); raw = P.credit - (P.short_strike - P.settle).clip(lower=0, upper=P.width)
    part = (P.settle <= P.short_strike) & (P.settle > P.long_strike)
    P["pnl"] = np.where(part & (raw > 0), raw * 0.5, raw) * 100; P["risk"] = (P.width - P.credit) * 100
    P["ror"] = P.pnl / P.risk; P["pos"] = (P.pnl > 0).astype(float); P["win"] = (P.settle >= P.short_strike).astype(float); return P
def add(P):
    v = [prev(d, h, t) for d, h, t in zip(P.day, P.hm, P.ticker)]
    P = P.copy(); P["share_q"], P["share_t"], P["n_q"], P["n_prior"] = zip(*v); return P[P.n_prior >= 8]
A = add(book(E[E.hm.between("1515", "1545")]))
key = ["ticker", "short_strike", "long_strike", "exp"]; out = []
for day, g in E.groupby("day"):
    a = g[g.hm.isin(["1530", "1531"])].sort_values("G24", ascending=False).head(TOP)
    b = g[g.hm == "1545"].sort_values("G24", ascending=False).head(TOP)
    have = set(map(tuple, a[key].values)); b = b[[tuple(r) not in have for r in b[key].values]]
    out.append(pd.concat([a, b.head(max(0, TOP - len(a)))]))
B = add(book(pd.concat(out)))
import statsmodels.formula.api as smf
def reg(P, y, x):
    P = P.copy(); P["cl"] = P.ticker + "|" + P.exp
    m = smf.ols(f"{y} ~ {x} + C(exp)", P).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(P.cl)[0]})
    return m.params[x], m.pvalues[x]
def show(lab, P):
    print(f"\n=== {lab}: n {len(P)}, days {P.day.nunique()}, weeks {P.exp.nunique()}, names {P.ticker.nunique()}, name-weeks {(P.ticker + P.exp).nunique()}")
    for x in ("share_q", "share_t"):
        print(f"  {x}: corr with P&L/risk {P[x].corr(P.ror):+.3f}, with positive {P[x].corr(P.pos):+.3f}; "
              + "; ".join(f"{y} slope {reg(P, y, x)[0]:+.3f} p={reg(P, y, x)[1]:.3f}" for y in ("ror", "pos")) + "  (week FE, clustered name x week)")
    P = P.copy(); P["b"] = pd.cut(P.share_q, [-0.01, 0.0, 0.25, 0.5, 1.0], labels=["0%", "1-25%", "26-50%", "51-100%"])
    t = P.groupby("b", observed=True).agg(n=("pnl", "size"), win=("win", "mean"), pos=("pos", "mean"), per_trade=("pnl", "mean"), pnl=("pnl", "sum"), risk=("risk", "sum"))
    t["on_risk"] = 100 * t.pnl / t.risk; t["win"] *= 100; t["pos"] *= 100
    print(t[["n", "win", "pos", "per_trade", "pnl", "on_risk"]].round(1).to_string())
show("A  every eligible spread at 15:15-15:45", A); show("B  canon picks (15:30 + 15:45 top-up)", B)
# weighting test on the canon picks: equal weight vs weight proportional to prevalence (same total contracts per day)
def wtest(P, x):
    P = P.copy(); P["w"] = P.groupby("day")[x].transform(lambda s: (s + 1e-9) / (s + 1e-9).sum() * len(s)); P.loc[P.groupby("day")[x].transform("sum") == 0, "w"] = 1.0
    wk_e = P.groupby("exp").pnl.sum(); wk_w = (P.pnl * P.w).groupby(P.exp).sum()
    return (f"  weight by {x}: equal ${P.pnl.sum():+.0f} ({100*P.pnl.sum()/P.risk.sum():+.1f}% on risk, {int((wk_e>0).sum())}/{len(wk_e)} wks, worst ${wk_e.min():+.0f})"
            f"  vs weighted ${(P.pnl*P.w).sum():+.0f} ({100*(P.pnl*P.w).sum()/(P.risk*P.w).sum():+.1f}% on risk, {int((wk_w>0).sum())}/{len(wk_w)} wks, worst ${wk_w.min():+.0f})")
print("\n=== weighting the canon picks"); print(wtest(B, "share_q")); print(wtest(B, "share_t"))
h = B[B.share_q > B.groupby("day").share_q.transform("median")]; l = B[B.share_q <= B.groupby("day").share_q.transform("median")]
print(f"  above the day's median prevalence: n {len(h)} ${h.pnl.sum():+.0f} {100*h.pnl.sum()/h.risk.sum():+.1f}% on risk, positive {100*h.pos.mean():.1f}%   at/below: n {len(l)} ${l.pnl.sum():+.0f} {100*l.pnl.sum()/l.risk.sum():+.1f}% on risk, positive {100*l.pos.mean():.1f}%")
