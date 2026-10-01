"""Kelly sizing sweep for the site book (strategy C, k=24, OI>=1, 1.00x model).

w* options:
  raw    - ent_canon.kelly(p,q,ro,b)                 (what the payload uses today)
  carry  - same growth, with CARRY_RATE x DTE/365 earned on the capital at risk:
           win pays b+carry, partial pays a*b+carry, loss costs (1-carry) of the stake.
Sizing: qty = max(1, min(CAP, int(frac * w * BANKROLL / max_loss_dollar))), the payload's own rule.
Metrics mirror report_mid_canon.build_payload (SPY calendar, weekly $-Sharpe, DD on equity, yield on dollars wagered).
"""
import sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
import ent_canon as ec, config as cfg, report_mid_canon as rmc
HERE = Path(__file__).resolve().parent
BANK = rmc.START_BANKROLL; SPY = pd.read_csv(ROOT / "data/spy_us_d.csv", parse_dates=["Date"]).sort_values("Date").reset_index(drop=True)

def w_star_carry(p, q, ro, b, carry):
    """argmax_w  p ln(1+w(b+c)) + ro ln(1+w(a b+c)) + q ln(1-w(1-c)),  a as in ent_canon.kelly."""
    p, q, ro, b, c = (np.asarray(x, float) for x in (p, q, ro, b, carry))
    a = np.where(b >= 1.0, 0.0, (b - 1.0) / (2.0 * np.where(b == 0, 1, b)))
    W = np.linspace(0.001, 0.99, 400)[None, :]
    g1 = (b + c)[:, None]; g2 = (a * b + c)[:, None]; L = (1.0 - c)[:, None]
    with np.errstate(invalid="ignore", divide="ignore"):
        ell = (p[:, None] * np.log(np.clip(1 + W * g1, 1e-12, None))
               + ro[:, None] * np.log(np.clip(1 + W * g2, 1e-12, None))
               + q[:, None] * np.log(np.clip(1 - W * L, 1e-12, None)))
    best = np.nanargmax(np.where(np.isfinite(ell), ell, -np.inf), axis=1)
    w = W[0][best]
    bad = ~(np.isfinite(b) & (p > 0) & (q > 0) & (p + q <= 1.0)) | (np.nanmax(ell, axis=1) <= 0)
    return np.where(bad, np.nan, w)

def metrics(picks, qty, end_year):
    start = picks.entry_date_dt.min().normalize()
    end = max(pd.Timestamp(f"{end_year}-12-31"), pd.to_datetime(picks.realize_date).max().normalize())
    td = pd.DatetimeIndex(SPY[(SPY.Date >= start) & (SPY.Date <= end)].Date)
    pnl = pd.Series((qty * picks.pnl_per_contract).values, index=pd.to_datetime(picks.realize_date)).groupby(level=0).sum()
    eq = BANK + pnl.reindex(td, fill_value=0.0).cumsum()
    w = eq.diff().fillna(0).resample("W-FRI").sum()
    dd = ((eq - eq.cummax()) / eq.cummax()).min()
    wag = float((qty * picks.max_loss_dollar).sum())
    return dict(final=round(float(eq.iloc[-1])), dsh=round(float(w.mean() * np.sqrt(52) / w.std(ddof=0)), 2),
                dd=round(100 * dd, 1), yield_pct=round(100 * (float(eq.iloc[-1]) - BANK) / wag, 2) if wag else 0,
                avg_qty=round(float(np.mean(qty)), 2), max_qty=int(np.max(qty)), wagered=round(wag))

def qty_fixed(w, ml, frac, cap):
    q = np.where(np.isfinite(w) & (w > 0) & (ml > 0), (frac * w * BANK / np.maximum(ml, 1e-9)).astype(int), 1)
    return np.clip(q, 1, cap)

def qty_compound(picks, w, frac, cap):
    """Kelly on the RUNNING bankroll, chronological."""
    bank = BANK; out = np.ones(len(picks), int)
    for i, (wi, ml, p) in enumerate(zip(w, picks.max_loss_dollar.values, picks.pnl_per_contract.values)):
        q = 1 if not np.isfinite(wi) or wi <= 0 or ml <= 0 else int(frac * wi * bank / ml)
        q = int(np.clip(q, 1, cap)); out[i] = q; bank = max(bank + q * p, 1.0)
    return out

P = pd.read_parquet(HERE / "picks_C_252_k24.parquet").sort_values("entry_date_dt").reset_index(drop=True)
P["carry"] = cfg.CARRY_RATE * np.clip(P.DTE.astype(float), 1, None) / 365.0
b = (P.model_credit / (P.width - P.model_credit)).values
P["w_raw"] = P.w_star.values
P["w_carry"] = w_star_carry(P.p.values, P.q.values, P.ro.values, b, P.carry.values)
chk = w_star_carry(P.p.values, P.q.values, P.ro.values, b, np.zeros(len(P)))
ok = np.isfinite(chk) & np.isfinite(P.w_raw)
print(f"solver check vs ent_canon.kelly at carry=0: median |diff| {np.nanmedian(np.abs(chk[ok]-P.w_raw.values[ok])):.4f} (grid step 0.0025), n {ok.sum()}")
print(f"w*: raw median {np.nanmedian(P.w_raw):.3f}, with carry {np.nanmedian(P.w_carry):.3f}; finite raw {100*np.isfinite(P.w_raw).mean():.0f}%, carry {100*np.isfinite(P.w_carry).mean():.0f}%; carry median {P.carry.median()*1e4:.2f} bps")
rows = []
for lab, m, yr in (("IS", P.entry_date.dt.year <= 2025, 2025), ("OOT", P.entry_date.dt.year == 2026, 2026)):
    x = P[m].reset_index(drop=True); ml = x.max_loss_dollar.values
    rows.append(dict(window=lab, arm="qty 1 (flat)", **metrics(x, np.ones(len(x)), yr)))
    rows.append(dict(window=lab, arm="qty 2 (site headline)", **metrics(x, 2*np.ones(len(x)), yr)))
    for wn, w in (("raw", x.w_raw.values), ("carry", x.w_carry.values)):
        for frac, fl in ((0.0625, "1/16"), (0.25, "1/4"), (0.5, "1/2"), (1.0, "1x")):
            rows.append(dict(window=lab, arm=f"{fl} Kelly {wn} (cap 5)", **metrics(x, qty_fixed(w, ml, frac, 5), yr)))
    for frac, fl in ((0.5, "1/2"), (1.0, "1x")):
        rows.append(dict(window=lab, arm=f"{fl} Kelly carry, cap 20", **metrics(x, qty_fixed(x.w_carry.values, ml, frac, 20), yr)))
        rows.append(dict(window=lab, arm=f"{fl} Kelly carry, compounding cap 20", **metrics(x, qty_compound(x, x.w_carry.values, frac, 20), yr)))
R = pd.DataFrame(rows); R.to_csv(HERE / "kelly_sizing_sweep.csv", index=False)
for lab in ("IS", "OOT"):
    print(f"\n{lab} ({'2021-25' if lab=='IS' else '2026'}), $10k start, 1.00x model:")
    print(R[R.window.eq(lab)].drop(columns=["window"]).to_string(index=False))
