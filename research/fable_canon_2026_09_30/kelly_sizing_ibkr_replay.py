"""Kelly sizing sweep on the IBKR fair replay (mini). Mirrors kelly_sizing_sweep.py (the Air's, vendor book).

Picks: every archived 15:30/15:45 IBKR snapshot 2026-08-20..09-24 re-ranked with the current canon
(ibkr_replay_candidates_1530_1545.csv), strategy C + quote gate: model credit >= 0.45 x width,
quoted mid >= model, pooled top 6 by G24, 15:30 then 15:45 top-up to 6. Bull puts only (SPY above its 100d).
Sizing rule and w* definitions are the Air's: qty = clip(int(frac * w * BANK / max_loss_dollar), 1, CAP).
Usage: python kelly_sizing_ibkr_replay.py [fill_mult=1.00]
"""
import sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
import ent_canon as ec, config as cfg
from spreads import settle_pnl
HERE = Path(__file__).resolve().parent
BANK = 10000.0; FILL = float(sys.argv[1]) if len(sys.argv) > 1 else 1.00

def w_star_carry(p, q, ro, b, carry):
    p, q, ro, b, c = (np.asarray(x, float) for x in (p, q, ro, b, carry))
    a = np.where(b >= 1.0, 0.0, (b - 1.0) / (2.0 * np.where(b == 0, 1, b)))
    W = np.linspace(0.001, 0.99, 400)[None, :]
    g1 = (b + c)[:, None]; g2 = (a * b + c)[:, None]; L = (1.0 - c)[:, None]
    with np.errstate(invalid="ignore", divide="ignore"):
        ell = (p[:, None] * np.log(np.clip(1 + W * g1, 1e-12, None)) + ro[:, None] * np.log(np.clip(1 + W * g2, 1e-12, None))
               + q[:, None] * np.log(np.clip(1 - W * L, 1e-12, None)))
    best = np.nanargmax(np.where(np.isfinite(ell), ell, -np.inf), axis=1); w = W[0][best]
    bad = ~(np.isfinite(b) & (p > 0) & (q > 0) & (p + q <= 1.0)) | (np.nanmax(ell, axis=1) <= 0)
    return np.where(bad, np.nan, w)

R = pd.read_csv(HERE / "ibkr_replay_candidates_1530_1545.csv", dtype={"hm": str})
R = R[R.settle.notna() & (R.exp <= "2026-09-25") & (R.model_credit > 0) & R.net_credit.notna() & R.p.notna()].copy()
R["slot"] = R.hm.map(lambda h: "15:30" if h in ("1530", "1531") else "15:45")
E = R[(R.model_cw >= 0.45) & (R.quote_over_model >= 1.0)].copy()
E["r"] = E.groupby(["day", "hm"]).G24.rank(ascending=False, method="first"); T = E[E.r <= 6]
key = ["day", "ticker", "short_strike", "long_strike", "exp"]; out = []
for day, g in T.groupby("day"):
    ga = g[g.slot == "15:30"]; gb = g[g.slot == "15:45"]; have = set(map(tuple, ga[key].values))
    add = gb[np.array([tuple(r) not in have for r in gb[key].values], dtype=bool)]
    out.append(pd.concat([ga, add.head(max(0, 6 - len(ga)))]))
P = pd.concat(out).sort_values(["day", "hm", "r"]).reset_index(drop=True)
P["credit"] = (FILL * P.model_credit).clip(upper=P.width - 0.01)
P["max_loss_dollar"] = (P.width - P.credit) * 100
P["pnl_per_contract"] = [settle_pnl(s, ks, kl, c, w - c, t) * 100 for s, ks, kl, c, w, t in
                         zip(P.settle, P.short_strike, P.long_strike, P.credit, P.width, P.spread_type)]
b = (P.model_credit / (P.width - P.model_credit)).values
P["w_raw"] = ec.kelly(P.p.values, P.q.values, P.ro.values, b)[0]
P["carry"] = cfg.CARRY_RATE * np.clip(P.DTE.astype(float), 1, None) / 365.0
P["w_carry"] = w_star_carry(P.p.values, P.q.values, P.ro.values, b, P.carry.values)
EXPS = sorted(P.exp.unique())

def metrics(x, qty):
    pnl = pd.Series(qty * x.pnl_per_contract.values, index=x.exp.values).groupby(level=0).sum().reindex(EXPS).fillna(0)
    eq = pd.concat([pd.Series([BANK]), (BANK + pnl.cumsum()).reset_index(drop=True)])
    wag = float((qty * x.max_loss_dollar.values).sum()); wk_risk = pd.Series(qty * x.max_loss_dollar.values, index=x.exp.values).groupby(level=0).sum()
    return dict(final=round(float(eq.iloc[-1])), pnl=round(float(pnl.sum())), dsh=round(float(pnl.mean() * np.sqrt(52) / pnl.std(ddof=0)), 2),
                dd=round(100 * float((eq / eq.cummax() - 1).min()), 1), yield_pct=round(100 * float(pnl.sum()) / wag, 2) if wag else 0,
                weeks_pos=f"{int((pnl > 0).sum())}/{len(pnl)}", worst_wk=round(float(pnl.min())), avg_qty=round(float(np.mean(qty)), 2),
                max_qty=int(np.max(qty)), max_wk_risk=round(float(wk_risk.max())))

def qty_fixed(w, ml, frac, cap):
    q = np.where(np.isfinite(w) & (w > 0) & (ml > 0), (frac * w * BANK / np.maximum(ml, 1e-9)).astype(int), 1)
    return np.clip(q, 1, cap)

def qty_compound(x, w, frac, cap):
    bank = BANK; o = np.ones(len(x), int)
    for i, (wi, ml, p) in enumerate(zip(w, x.max_loss_dollar.values, x.pnl_per_contract.values)):
        q = 1 if not np.isfinite(wi) or wi <= 0 or ml <= 0 else int(frac * wi * bank / ml)
        q = int(np.clip(q, 1, cap)); o[i] = q; bank = max(bank + q * p, 1.0)
    return o

print(f"IBKR fair replay, C + quote gate, k=24: {len(P)} picks, {P.day.nunique()} days, booked {FILL:.2f}x model, $10k start")
print(f"w*: raw median {np.nanmedian(P.w_raw):.3f} (at the 0.01 floor: {100*np.mean(np.isclose(P.w_raw, 0.01)):.0f}%), "
      f"with carry median {np.nanmedian(P.w_carry):.3f}, finite {100*np.isfinite(P.w_carry).mean():.0f}%; carry median {P.carry.median()*1e4:.2f} bps")
ml = P.max_loss_dollar.values; rows = [dict(arm="qty 1 (flat)", **metrics(P, np.ones(len(P)))), dict(arm="qty 2", **metrics(P, 2 * np.ones(len(P))))]
for wn, w in (("raw", P.w_raw.values), ("carry", P.w_carry.values)):
    for frac, fl in ((0.0625, "1/16"), (0.25, "1/4"), (0.5, "1/2"), (1.0, "1x")):
        rows.append(dict(arm=f"{fl} Kelly {wn} (cap 5)", **metrics(P, qty_fixed(w, ml, frac, 5))))
for frac, fl in ((0.5, "1/2"), (1.0, "1x")):
    rows.append(dict(arm=f"{fl} Kelly carry, cap 20", **metrics(P, qty_fixed(P.w_carry.values, ml, frac, 20))))
    rows.append(dict(arm=f"{fl} Kelly carry, compounding cap 20", **metrics(P, qty_compound(P, P.w_carry.values, frac, 20))))
Rr = pd.DataFrame(rows); Rr.to_csv(HERE / f"kelly_sizing_ibkr_replay_fill{FILL:.2f}.csv", index=False)
pd.set_option("display.width", 220); print(Rr.to_string(index=False))
q = qty_fixed(P.w_carry.values, ml, 0.5, 5); P["qty"] = q
print("\n1/2 Kelly carry (cap 5): P&L by size bucket:"); print(P.assign(pnl=q * P.pnl_per_contract).groupby("qty").agg(n=("pnl", "size"), win=("pnl", lambda s: (s > 0).mean()), per_contract=("pnl_per_contract", "mean"), pnl=("pnl", "sum")).round(2).to_string())
