"""IBKR fair replay re-scored with the VENDOR EOD smile fit (model credit, fitted deltas, D_ent) on the same IBKR strikes.
Everything else IBKR: candidates, 15:30/15:45 spot, quoted mid, P_real. Selection = replay rule: model_cw >= 0.45,
quote >= model, pooled top 6 by G24 at 15:30 then 15:45 top-up; booked 1.04 x model; settle = CSV settle."""
import sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); import ent_canon as ec, config as cfg
HERE = Path(__file__).resolve().parent; K = 24.0
I = pd.read_csv(HERE / "ibkr_replay_candidates_1530_1545.csv"); I["entry_date"] = pd.to_datetime(I.day); I["expiry_date"] = pd.to_datetime(I.exp)
COLS = ["Symbol","DataDate","ExpirationDate","PutCall","StrikePrice","BidPrice","AskPrice","ImpliedVolatility","UnderlyingPrice"]
ch = pd.read_parquet(ROOT / ec.vendor_year_parquet(2026), columns=COLS)
ch["DataDate"] = pd.to_datetime(ch.DataDate).dt.normalize(); ch["ExpirationDate"] = pd.to_datetime(ch.ExpirationDate).dt.normalize()
ch = ch[ch.DataDate.isin(set(I.entry_date)) & ch.Symbol.isin(set(I.ticker))].copy(); ch["DTE"] = (ch.ExpirationDate - ch.DataDate).dt.days
for c in COLS[4:]: ch[c] = pd.to_numeric(ch[c], errors="coerce")
ch["PutCall"] = ch.PutCall.astype(str).str.lower().str.strip()
fits = ec.fit_smiles(ch)
def settle_pnl(d, cr):
    part = cr - (d.short_strike - d.settle); part = np.where(part > 0, 0.5 * part, part)
    return np.where(d.settle > d.short_strike, cr, np.where(d.settle <= d.long_strike, -(d.width - cr), part)) * 100
def select(D, mc, dent, label):
    D = D.copy(); D["mc"] = mc; D["dent"] = dent
    b = D.mc / (D.width - D.mc); _, ell = ec.kelly(D.p.values, D.q.values, D.ro.values, b.values); D["EV2"] = np.exp(ell) - 1
    D["G24_2"] = D.EV2 * np.exp(-K * D.dent); D["elig"] = ((D.mc / D.width) >= 0.45) & (D.net_credit >= D.mc) & D.G24_2.notna() & (D.width - D.mc > 0)
    s30 = D[D.hm.isin([1530, 1531])].sort_values(["day", "hm"]).drop_duplicates(["day", "ticker"]).sort_values(["day", "G24_2"], ascending=[True, False])
    s45 = D[D.hm.eq(1545)].sort_values(["day", "G24_2"], ascending=[True, False]); picks = []
    for day, g in s30.groupby("day"):
        base = g[g.elig].head(6); names = set(base.ticker); rows = [base]
        if len(base) < 6: rows.append(s45[(s45.day == day) & s45.elig & ~s45.ticker.isin(names)].head(6 - len(base)))
        picks.append(pd.concat(rows))
    P = pd.concat(picks); P["pnl"] = settle_pnl(P, 1.04 * P.mc); P["wk"] = P.exp
    wk = P.groupby("wk").pnl.sum()
    print(f"{label:<44} picks {len(P):>3}  P&L ${P.pnl.sum():>6,.0f}  win {100 * (P.settle > P.short_strike).mean():.0f}%  full-loss {100 * (P.settle <= P.long_strike).mean():.0f}%  weeks + {int((wk > 0).sum())}/{len(wk)}  worst wk ${wk.min():,.0f}")
    return P
# 0. original replay (IBKR smile)
P0 = select(I, I.model_credit, I.D_ent, "IBKR replay as built (IBKR 15:30 smile)")
# 1. vendor EOD smile priced at the IBKR spot
C1 = I[["ticker", "entry_date", "expiry_date", "DTE", "spread_type", "entry_price", "short_strike", "long_strike"]].copy()
X1 = ec.price_spreads(C1, fits); ok1 = X1.model_credit.notna()
print(f"vendor EOD smile available for {100 * ok1.mean():.0f}% of IBKR rows; model credit EOD/IBKR median {(X1.model_credit / I.model_credit).median():.3f}, p10-p90 {(X1.model_credit / I.model_credit).quantile(.1):.3f}-{(X1.model_credit / I.model_credit).quantile(.9):.3f}")
P1 = select(I[ok1.values], X1.model_credit[ok1].values, X1.D_ent[ok1].values, "IBKR replay, vendor EOD smile @ IBKR spot")
# 2. vendor EOD smile priced at the vendor EOD spot
sp = ch.groupby(["Symbol", "DataDate"]).UnderlyingPrice.first().reset_index().rename(columns={"Symbol": "ticker", "DataDate": "entry_date", "UnderlyingPrice": "spot_eod"})
C2 = C1.merge(sp, on=["ticker", "entry_date"], how="left"); C2["entry_price"] = C2.spot_eod.fillna(C2.entry_price)
X2 = ec.price_spreads(C2.drop(columns=["spot_eod"]), fits); ok2 = X2.model_credit.notna()
P2 = select(I[ok2.values], X2.model_credit[ok2].values, X2.D_ent[ok2].values, "IBKR replay, vendor EOD smile @ EOD spot")
# overlaps
V = pd.read_parquet(HERE / "picks_C_252_k24.parquet"); V["day"] = V.entry_date.dt.strftime("%Y-%m-%d"); V = V[V.day.isin(set(P0.day)) & V.spread_type.eq("bull_put")]
pv = set(zip(V.day, V.ticker)); days = set(V.day)
for lab, P in (("as built", P0), ("EOD smile @ IBKR spot", P1), ("EOD smile @ EOD spot", P2)):
    s = set(zip(P.day, P.ticker)); s = {x for x in s if x[0] in days}; s0 = {x for x in zip(P0.day, P0.ticker) if x[0] in days}
    print(f"overlap {lab:<22}: with original replay {len(s & s0):>3}/{len(s0)} ({100 * len(s & s0) / len(s0):.0f}%) | with vendor site book {len(s & pv):>3}/{len(pv)} ({100 * len(s & pv) / len(pv):.0f}%)")
P1.to_csv(HERE / "ibkr_replay_eod_smile_picks.csv", index=False)
