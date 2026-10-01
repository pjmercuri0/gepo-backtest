"""Vendor picks (strategy C, k=24, OI>=1, no gate) over 2026-08-20..09-24 with the IBKR 15:30 spot substituted for the vendor EOD spot.
A: keep the vendor strikes, re-price (model credit, D_ent, P_real) on the IBKR spot.
B: rebuild the strikes from the vendor chain with UnderlyingPrice := IBKR spot, then price/score.
Overlap vs the IBKR fair-replay picks and P&L at 1.04 x model."""
import sys, io, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research")); sys.path.insert(0, str(ROOT / "research/dkl_2026_09_13"))
import ent_canon as ec, config as cfg
from sma_bull_regime_sweep import GAP_SERIES
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate
HERE = Path(__file__).resolve().parent; K = 24.0; W0, W1 = "2026-08-20", "2026-09-24"
MIN_OI = int(sys.argv[1]) if len(sys.argv) > 1 else 1; FRAME_FILE = {1: "featATM8_oi1.parquet", 0: "featATM8_oi0.parquet", 100: "featATM8.parquet"}[MIN_OI]
print(f"vendor chain OI >= {MIN_OI} ({FRAME_FILE})")
I = pd.read_csv(HERE / "ibkr_replay_candidates_1530_1545.csv")
spot = (I.sort_values(["day", "hm"]).groupby(["day", "ticker"]).entry_price.first().reset_index().rename(columns={"entry_price": "spot_ibkr"}))   # 1530 > 1531 > 1545
J = pd.read_csv(HERE / "reconcile_vendor_ibkr.csv"); both = sorted(set(J[J._merge.eq("both")].day))
pi = set(zip(J[J.pick_i.astype(bool) & J.day.isin(both)].day, J[J.pick_i.astype(bool) & J.day.isin(both)].ticker))
closes = ec.backtest_closes(); cl = pd.read_parquet(ROOT / "output/daily_closes.parquet"); cl["date"] = pd.to_datetime(cl.date).dt.normalize()
gaps = pd.read_parquet(GAP_SERIES)

def score_and_pick(C, label):
    C = C.copy(); C["day"] = C.entry_date.dt.strftime("%Y-%m-%d"); C = C[C.day.isin(both)]
    C = C.drop(columns=[x for x in ("expiry_close", "p", "q", "ro", "EV", "G", "GROUND", "w_star", "DKL", "qualified", "outcome", "win") if x in C.columns])
    C = C.merge(cl.rename(columns={"date": "expiry_date", "close": "expiry_close"}), on=["ticker", "expiry_date"], how="left").dropna(subset=["expiry_close"])
    C = C[(C.width - C.model_credit) > 0]
    C = add_exdiv_gate(C); C = add_earnings_gate(C); C = C[~C.exdiv_hit & ~C.earnings_hit]
    mu = ec.gap_drift(C, closes, gaps)
    with contextlib.redirect_stdout(io.StringIO()):
        S = ec.score(C, closes, k=K, mu=mu)
    S["G24"] = S.EV * np.exp(-K * S.D_ent); S = S[(S.model_credit / S.width) >= 0.45].dropna(subset=["G24"])
    top = S.sort_values(["day", "G24"], ascending=[True, False]).groupby("day").head(6)
    S.to_parquet(HERE / f"scored_{label[:1]}_oi{MIN_OI}.parquet")
    cr = 1.04 * top.model_credit; part = cr - (top.short_strike - top.expiry_close); part = np.where(part > 0, 0.5 * part, part)
    pnl = np.where(top.expiry_close > top.short_strike, cr, np.where(top.expiry_close <= top.long_strike, -(top.width - cr), part)) * 100
    pv = set(zip(top.day, top.ticker)); ov = pv & pi
    print(f"{label}: candidates {len(S)}, picks {len(pv)}, overlap with IBKR {len(ov)} ({100 * len(ov) / len(pi):.0f}% of IBKR's {len(pi)}), "
          f"P&L qty1 ${pnl.sum():,.0f}, win {100 * (top.expiry_close > top.short_strike).mean():.0f}%, full-loss {100 * (top.expiry_close <= top.long_strike).mean():.0f}%")
    return top

# baseline: vendor frame as is (OI>=1), window
F = pd.read_parquet(ROOT / "research/dkl_2026_09_13" / FRAME_FILE); F["entry_date"] = pd.to_datetime(F.entry_date).dt.normalize(); F["expiry_date"] = pd.to_datetime(F.expiry_date).dt.normalize()
F = F[(F.entry_date >= W0) & (F.entry_date <= W1) & F.spread_type.eq("bull_put")].copy(); F["day"] = F.entry_date.dt.strftime("%Y-%m-%d")
F = F.merge(spot, on=["day", "ticker"], how="inner")   # only names with an IBKR spot that day
base = score_and_pick(F.drop(columns=["spot_ibkr"]), "vendor EOD spot, vendor strikes (names with an IBKR spot)")
# A: same strikes, IBKR spot
A = F.copy(); A["entry_price"] = A.spot_ibkr
fits = A[["ticker", "entry_date", "expiry_date", "c0", "c1", "c2", "sig0", "fit_n", "fit_rmse"]].drop_duplicates().rename(columns={"ticker": "Symbol", "entry_date": "DataDate", "expiry_date": "ExpirationDate"})
A = ec.price_spreads(A[["ticker", "entry_date", "expiry_date", "DTE", "spread_type", "entry_price", "short_strike", "long_strike", "width"]], fits)
A["max_loss"] = A.width - A.model_credit
a = score_and_pick(A, "A: IBKR 15:30 spot, vendor strikes")
# B: rebuild strikes on the IBKR spot from the vendor chain
import build_frame as bf
COLS = bf.COLS
ch = pd.read_parquet(ROOT / ec.vendor_year_parquet(2026), columns=COLS)
ch["DataDate"] = pd.to_datetime(ch.DataDate).dt.normalize(); ch["ExpirationDate"] = pd.to_datetime(ch.ExpirationDate).dt.normalize()
ch = ch[(ch.DataDate >= W0) & (ch.DataDate <= W1) & ch.Symbol.isin(set(cfg.SP100_TICKERS))].copy(); ch["DTE"] = (ch.ExpirationDate - ch.DataDate).dt.days
ch = ch[ch.DTE.between(1, 4) & (ch.ExpirationDate.dt.dayofweek == 4)]
for c in COLS[4:]: ch[c] = pd.to_numeric(ch[c], errors="coerce")
ch["PutCall"] = ch.PutCall.astype(str).str.lower().str.strip(); ch["day"] = ch.DataDate.dt.strftime("%Y-%m-%d")
ch = ch.merge(spot.rename(columns={"ticker": "Symbol"}), on=["day", "Symbol"], how="inner"); ch["UnderlyingPrice"] = ch.spot_ibkr
fitsB = ec.fit_smiles(ch)
q = ch[(ch.BidPrice > 0) & (ch.AskPrice > ch.BidPrice) & (ch.OpenInterest.fillna(0) >= MIN_OI) & ch.PutCall.eq("put")].sort_values(["Symbol", "DataDate", "ExpirationDate", "StrikePrice"])
q["lo"] = q.groupby(["Symbol", "DataDate", "ExpirationDate"]).StrikePrice.shift(1); q = q.dropna(subset=["lo"])
cand = q.rename(columns={"Symbol": "ticker", "DataDate": "entry_date", "ExpirationDate": "expiry_date", "StrikePrice": "short_strike", "UnderlyingPrice": "entry_price"})
cand = cand.assign(spread_type="bull_put", long_strike=cand.lo.astype(float))[["ticker", "entry_date", "expiry_date", "DTE", "spread_type", "entry_price", "short_strike", "long_strike"]]
cand["short_strike"] = cand.short_strike.astype(float); cand["width"] = (cand.short_strike - cand.long_strike).abs().round(4); cand = cand[cand.width <= cfg.MAX_SPREAD_WIDTH + 1e-9]
P = ec.price_spreads(cand, fitsB); b = P[P.dfit_short.between(bf.LO, bf.HI) & (P.dfit_long < P.dfit_short) & (P.model_credit > 0.01)].copy()
b["dist"] = (b.dfit_short - bf.TGT).abs(); B = b.loc[b.groupby(["ticker", "entry_date", "expiry_date"], sort=False)["dist"].idxmin()].copy(); B["max_loss"] = B.width - B.model_credit
bp = score_and_pick(B, "B: IBKR 15:30 spot, strikes rebuilt on it")
# strike agreement for B vs IBKR
Ii = I[I.hm.isin([1530, 1531])].drop_duplicates(["day", "ticker"]); bp2 = bp.merge(Ii[["day", "ticker", "short_strike", "long_strike"]], on=["day", "ticker"], suffixes=("", "_i"))
print(f"B picks also built by IBKR: {len(bp2)}; same strikes {100 * ((bp2.short_strike == bp2.short_strike_i) & (bp2.long_strike == bp2.long_strike_i)).mean():.0f}%")

# how much of the remaining gap is the IBKR quote gate? IBKR 15:30 top-6 WITHOUT the gate vs B
Ii2 = Ii[Ii.model_cw >= 0.45].sort_values(["day", "G24"], ascending=[True, False]).groupby("day").head(6)
pi_ng = set(zip(Ii2.day, Ii2.ticker)) & {(d, t) for d, t in zip(Ii2.day, Ii2.ticker) if d in both}
for lab, top in (("A", a), ("B", bp)):
    pv = set(zip(top.day, top.ticker)); print(f"{lab} vs IBKR top-6 WITHOUT quote gate: overlap {len(pv & pi_ng)} ({100 * len(pv & pi_ng) / len(pi_ng):.0f}% of {len(pi_ng)})")
# B scored vs IBKR scored, same (day,ticker): rank corr of G24, EV, D_ent, model credit ratio
SB = pd.read_parquet(HERE / f"scored_B_oi{MIN_OI}.parquet"); m = SB.merge(Ii, on=["day", "ticker"], suffixes=("_b", "_i"))
same = (m.short_strike_b == m.short_strike_i) & (m.long_strike_b == m.long_strike_i); m = m[same]
rc = lambda a, b: m.groupby("day").apply(lambda g: g[a].rank().corr(g[b].rank()) if len(g) > 4 else np.nan).median()
print(f"B vs IBKR, identical strikes ({len(m)} pairs): within-day rank corr G24 {rc('G24_b','G24_i'):.2f}, EV {rc('EV_b','EV_i'):.2f}, D_ent {rc('D_ent_b','D_ent_i'):.2f}; "
      f"p corr {m.p_b.corr(m.p_i):.2f}; model credit B/IBKR p10-p90 {(m.model_credit_b/m.model_credit_i).quantile(.1):.3f}-{(m.model_credit_b/m.model_credit_i).quantile(.9):.3f}; "
      f"IBKR gate pass among these {100*(m.net_credit>=m.model_credit_i).mean():.0f}%")
