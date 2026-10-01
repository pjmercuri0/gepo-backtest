"""Strategy C (k=24, OI>=1, 252 start, no gate, fill argv[1] default 1.0) with the credit = Black-Scholes at the SHORT leg's
vendor IV applied to both legs (no smile fit). Variant 1: swap the credit only (floor, Kelly b, booking). Variant 2: also D_ent
from Q_bs at that single IV. Payloads to this dir only."""
import sys, io, json, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
HERE = Path(__file__).resolve().parent; KEY = ["ticker", "entry_date", "expiry_date", "spread_type", "short_strike", "long_strike"]
K = 24.0; FILL = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0; FLOOR, BEAR_IV, TOP = 0.45, 0.35, 6
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
c = c.merge(pd.read_parquet(HERE / "frame_quotes_oi1.parquet")[KEY + ["IV", "long_IV"]], on=KEY, how="left")
cl = pd.read_parquet(ROOT / "output/daily_closes.parquet"); cl["date"] = pd.to_datetime(cl.date).dt.normalize()
cl = cl.dropna(subset=["close"]).sort_values(["ticker", "date"]); cl["n_before"] = cl.groupby("ticker").cumcount()
c = c.merge(cl[["ticker", "date", "n_before"]].rename(columns={"date": "entry_date"}), on=["ticker", "entry_date"], how="left"); c = c[c.n_before >= ec.WINDOW].copy()
T = np.clip(c.DTE.values.astype(float), 1, None) / 365.0; S = c.entry_price.values.astype(float); isput = c.spread_type.eq("bull_put").values
iv = np.clip(c.IV.values.astype(float), ec.IV_LO, ec.IV_HI)
c["credit_1iv"] = (ec.bs_price(S, c.short_strike.values.astype(float), iv, T, isput) - ec.bs_price(S, c.long_strike.values.astype(float), iv, T, isput)).round(4)
Q1 = ec.market_triple(c, iv, iv); c["D_ent_1iv"] = ec.d_ent(Q1)
ok = c.credit_1iv.notna() & (c.credit_1iv > 0.01) & (c.width - c.credit_1iv > 0)
r = c.credit_1iv / c.model_credit
print(f"frame {len(c):,} (252 gate): single-IV credit vs smile credit corr {c.credit_1iv.corr(c.model_credit):.3f}, ratio median {r.median():.3f}, p10-p90 {r.quantile(.1):.2f}-{r.quantile(.9):.2f}; usable {100 * ok.mean():.1f}%")
closes = ec.backtest_closes()
def book(name, credit_col, dent_col):
    d = c[ok].copy(); d["model_credit"] = d[credit_col]; d["D_ent"] = d[dent_col]
    b = d.model_credit / (d.width - d.model_credit)
    _, ell = ec.kelly(np.nan_to_num(d.p.values), np.nan_to_num(d.q.values), np.nan_to_num(d.ro.values), b.values)
    d["EV"] = np.exp(ell) - 1; d["GROUND"] = d.EV * np.exp(-K * d.D_ent)
    g = ~d.exdiv_hit & ~d.earnings_hit; cw = d.model_credit / d.width
    elig = (d.spread_type.eq("bull_put") & g & (cw >= FLOOR)) | (d.spread_type.eq("bear_call") & g & (cw >= FLOOR) & (d.IV < BEAR_IV) & d.below_100)
    sel = d[elig].sort_values(["entry_date", "GROUND"], ascending=[True, False]).groupby("entry_date", sort=False).head(TOP)
    picks = rbr.enrich(realize(sel, 10**6, fill=FILL)); picks.to_parquet(HERE / f"picks_{name}.parquet")
    out = []
    for lab, m, yr in (("IS", picks.entry_date.dt.year <= 2025, 2025), ("OOT", picks.entry_date.dt.year == 2026, 2026)):
        p = picks[m]
        with contextlib.redirect_stdout(io.StringIO()):
            s = rmc.build_payload(p, yr, name)["summary"]
        out.append(f"{lab} {s['n_trades']} / ${s['strategy_final']:,.0f} / {s['strategy_sharpe_dollar']} / {s['strategy_max_dd']}% / yield {s['strategy_yield']}% / P&L>0 {100 * (p.pnl_per_contract > 0).mean():.0f}%")
    print(f"{name:<28} elig/day {elig.sum() / d.entry_date.nunique():.1f} | " + " | ".join(out))
    return picks
base = book("smile_credit (current)", "model_credit", "D_ent")
v1 = book("1iv_credit", "credit_1iv", "D_ent")
v2 = book("1iv_credit+1iv_Dent", "credit_1iv", "D_ent_1iv")
K2 = ["ticker", "entry_date", "spread_type", "short_strike", "long_strike"]
for nm, v in (("1iv_credit", v1), ("1iv_credit+1iv_Dent", v2)):
    a = set(map(tuple, base[K2].astype(str).values)); b2 = set(map(tuple, v[K2].astype(str).values)); print(f"pick overlap {nm} vs smile: {100 * len(a & b2) / len(a):.0f}%")
