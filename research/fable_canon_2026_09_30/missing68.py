"""Why are 68 IBKR fair-replay picks absent from the vendor frame? Replay build_frame.build_year on those (day,ticker)."""
import sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); import ent_canon as ec, config as cfg
HERE = Path(__file__).resolve().parent
J = pd.read_csv(HERE / "reconcile_vendor_ibkr.csv"); both = set(J[J._merge.eq("both")].day)
M = J[J._merge.eq("right_only") & J.pick_i.astype(bool) & J.day.isin(both)][["day", "ticker", "ss_i", "ls_i", "w_i", "entry_price", "quote_i", "model_i", "IV_i"]].copy()
print("IBKR picks missing from vendor frame:", len(M))
COLS = ["Symbol","DataDate","ExpirationDate","PutCall","StrikePrice","BidPrice","AskPrice","LastPrice","ImpliedVolatility","UnderlyingPrice","Delta","OpenInterest"]
ch = pd.read_parquet(ROOT / ec.vendor_year_parquet(2026), columns=COLS)
ch["DataDate"] = pd.to_datetime(ch.DataDate).dt.normalize(); ch["ExpirationDate"] = pd.to_datetime(ch.ExpirationDate).dt.normalize()
ch["PutCall"] = ch.PutCall.astype(str).str.lower().str.strip(); ch["DTE"] = (ch.ExpirationDate - ch.DataDate).dt.days
ch = ch[ch.Symbol.isin(set(M.ticker)) & ch.DataDate.isin(set(pd.to_datetime(M.day))) & ch.DTE.between(1, 4) & (ch.ExpirationDate.dt.dayofweek == 4)].copy()
for c in COLS[4:]: ch[c] = pd.to_numeric(ch[c], errors="coerce")
fits = ec.fit_smiles(ch)
rows = []
for r in M.itertuples():
    d = pd.Timestamp(r.day); x = ch[(ch.Symbol == r.ticker) & (ch.DataDate == d) & ch.PutCall.eq("put")]
    rec = dict(day=r.day, ticker=r.ticker, ibkr_ss=r.ss_i, ibkr_ls=r.ls_i, ibkr_spot=r.entry_price)
    if x.empty: rec["stage"] = "1 no vendor put rows (day/expiry)"; rows.append(rec); continue
    rec["vendor_spot"] = float(x.UnderlyingPrice.iloc[0]); rec["spot_move_pct"] = round(100 * (rec["vendor_spot"] / r.entry_price - 1), 2)
    s = x[x.StrikePrice == r.ss_i]; l = x[x.StrikePrice == r.ls_i]
    rec["short_in_chain"] = len(s) > 0; rec["long_in_chain"] = len(l) > 0
    if len(s): rec["short_oi"] = float(s.OpenInterest.iloc[0]); rec["short_bid"] = float(s.BidPrice.iloc[0]); rec["short_ask"] = float(s.AskPrice.iloc[0]); rec["short_vdelta"] = float(abs(s.Delta.iloc[0]))
    if len(l): rec["long_oi"] = float(l.OpenInterest.iloc[0]); rec["long_bid"] = float(l.BidPrice.iloc[0]); rec["long_ask"] = float(l.AskPrice.iloc[0])
    liq = x[(x.BidPrice > 0) & (x.AskPrice > x.BidPrice) & (x.OpenInterest.fillna(0) >= cfg.MIN_OPEN_INTEREST)]
    rec["n_liquid_puts"] = len(liq)
    if liq.empty: rec["stage"] = "2 no liquid put (OI>=100, bid>0, ask>bid)"; rows.append(rec); continue
    # adjacent-strike pairs among liquid puts, width <= 2.5, then fitted delta band
    liq = liq.sort_values("StrikePrice"); liq["lo"] = liq.StrikePrice.shift(1)
    pairs = liq.dropna(subset=["lo"]).copy(); pairs["width"] = (pairs.StrikePrice - pairs.lo).round(4); pairs = pairs[pairs.width <= cfg.MAX_SPREAD_WIDTH + 1e-9]
    if pairs.empty: rec["stage"] = "3 no adjacent liquid pair with width <= 2.5"; rows.append(rec); continue
    cand = pd.DataFrame({"ticker": r.ticker, "entry_date": d, "expiry_date": pairs.ExpirationDate.values, "DTE": pairs.DTE.values, "spread_type": "bull_put",
                         "entry_price": pairs.UnderlyingPrice.values, "short_strike": pairs.StrikePrice.values.astype(float), "long_strike": pairs.lo.values.astype(float)})
    P = ec.price_spreads(cand, fits)
    rec["n_pairs"] = len(P); rec["dfit_range"] = f"{P.dfit_short.min():.2f}-{P.dfit_short.max():.2f}"
    band = P[P.dfit_short.between(0.50, 0.60) & (P.dfit_long < P.dfit_short) & (P.model_credit > 0.01)]
    if band.empty: rec["stage"] = "4 no pair with fitted short delta in 0.50-0.60"; rows.append(rec); continue
    b = band.loc[(band.dfit_short - 0.55).abs().idxmin()]
    rec["stage"] = "5 built but lost downstream (max_loss, expiry close, prepare)"; rec["built_ss"] = float(b.short_strike); rec["built_ls"] = float(b.long_strike); rows.append(rec)
R = pd.DataFrame(rows); R.to_csv(HERE / "missing68.csv", index=False)
print(R.stage.value_counts().to_string()); print()
s4 = R[R.stage.str.startswith("4")]
print("stage-4 detail: short strike in chain %.0f%%, short OI>=100 %.0f%%, long OI>=100 %.0f%%, vendor |delta| at IBKR short strike median %.2f, spot move 15:30->EOD median %+.2f%% (abs median %.2f%%)" % (
    100 * s4.short_in_chain.mean(), 100 * (s4.short_oi >= 100).mean(), 100 * (s4.long_oi >= 100).mean(), s4.short_vdelta.median(), s4.spot_move_pct.median(), s4.spot_move_pct.abs().median()))
print("fitted-delta ranges of the liquid pairs (stage 4):", s4.dfit_range.value_counts().head(8).to_dict())
s2 = R[R.stage.str.startswith("2")]
if len(s2): print("stage-2 detail: short strike present %.0f%%, short OI median %s, long OI median %s" % (100 * s2.short_in_chain.mean(), s2.short_oi.median(), s2.long_oi.median()))
print(); print(R[["day","ticker","ibkr_ss","ibkr_ls","ibkr_spot","vendor_spot","short_oi","long_oi","short_vdelta","n_liquid_puts","dfit_range","stage"]].head(25).to_string(index=False))
