"""Extend the candidate frame past its last entry date from the vendor chains.

featATM6 was built by scripts that no longer exist (handoff 0.56/0.57).  This is the
replacement builder, validated 2026-09-19 by rebuilding AUGUST and diffing against
featATM6: 0 missing rows, model_credit and D_ent exact to 1e-6, 98.8% identical strikes
at OpenInterest >= config.MIN_OPEN_INTEREST.

Rules recovered from band_checks.build(): short leg fitted delta in [0.50, 0.60] nearest
0.55, long leg = adjacent strike, model_credit > 0.01, max_loss > 0, one row per
(ticker, entry, expiry, side), Friday expiries, DTE 1-4.

NOT recovered: the source ticker pool.  featATM6 draws on 80 names for 2026, not the full
SP100, and its pool file (oot_pairs_q.parquet) also has no builder.  This script FREEZES
the pool to the tickers already in the frame so old and new rows stay consistent.

    python3 research/dkl_2026_09_13/extend_frame.py
"""
import sys; sys.path.insert(0, "/Users/mercurio/Downloads/gepo-backtest")
import numpy as np, pandas as pd, ent_canon as ec, config as cfg

ROOT="/Users/mercurio/Downloads/gepo-backtest"; LO,HI,TGT=0.50,0.60,0.55; MIN_OI=cfg.MIN_OPEN_INTEREST
F=pd.read_parquet(f"{ROOT}/research/dkl_2026_09_13/featATM6.parquet")
F["entry_date"]=pd.to_datetime(F.entry_date).dt.normalize(); F["expiry_date"]=pd.to_datetime(F.expiry_date).dt.normalize()
LAST=F.entry_date.max(); POOL=sorted(F[F.entry_date.dt.year==2026].ticker.unique())
print(f"featATM6 ends {LAST.date()}, 2026 pool {len(POOL)} tickers")

cols=["Symbol","DataDate","ExpirationDate","PutCall","StrikePrice","BidPrice","AskPrice","LastPrice",
      "ImpliedVolatility","UnderlyingPrice","Delta","Gamma","Vega","Theta","OpenInterest"]
ch=pd.read_parquet(f"{ROOT}/{ec.vendor_year_parquet(2026)}", columns=cols)
ch["DataDate"]=pd.to_datetime(ch.DataDate).dt.normalize(); ch["ExpirationDate"]=pd.to_datetime(ch.ExpirationDate).dt.normalize()
ch=ch[(ch.DataDate>LAST)&ch.Symbol.isin(POOL)].copy()
ch["DTE"]=(ch.ExpirationDate-ch.DataDate).dt.days
ch=ch[ch.DTE.between(1,4)&(ch.ExpirationDate.dt.dayofweek==4)]
for c in cols[4:]: ch[c]=pd.to_numeric(ch[c],errors="coerce")
ch["PutCall"]=ch.PutCall.astype(str).str.lower().str.strip()
print(f"new chain rows {len(ch):,}  dates {sorted(ch.DataDate.dt.strftime('%m-%d').unique())}")

fits=ec.fit_smiles(ch)
rows=[]
for (sym,dd,ed),g in ch.groupby(["Symbol","DataDate","ExpirationDate"],sort=False):
    S=float(g.UnderlyingPrice.iloc[0]); dte=int(g.DTE.iloc[0])
    for right,st in (("put","bull_put"),("call","bear_call")):
        q=g[g.PutCall.eq(right)&(g.BidPrice>0)&(g.AskPrice>g.BidPrice)&(g.OpenInterest.fillna(0)>=MIN_OI)]
        k=np.sort(q.StrikePrice.unique())
        if len(k)<2: continue
        pr=[(k[i],k[i-1]) for i in range(1,len(k))] if st=="bull_put" else [(k[i],k[i+1]) for i in range(len(k)-1)]
        for ss,ls in pr: rows.append((sym,dd,ed,dte,st,S,float(ss),float(ls)))
cand=pd.DataFrame(rows,columns=["ticker","entry_date","expiry_date","DTE","spread_type","entry_price","short_strike","long_strike"])
cand["width"]=(cand.short_strike-cand.long_strike).abs().round(4)
P=ec.price_spreads(cand,fits)
b=P[P.dfit_short.between(LO,HI)&(P.dfit_long<P.dfit_short)&(P.model_credit>0.01)].copy()
b["dist"]=(b.dfit_short-TGT).abs()
C=b.loc[b.groupby(["ticker","entry_date","expiry_date","spread_type"],sort=False)["dist"].idxmin()].copy()
C["net_credit"]=C.model_credit; C["max_loss"]=(C.width-C.model_credit).round(4); C=C[C.max_loss>0].copy()

# expiry_close from the close store
cl=pd.read_parquet(f"{ROOT}/output/daily_closes.parquet"); cl["date"]=pd.to_datetime(cl.date).dt.normalize()
C=C.merge(cl.rename(columns={"date":"expiry_date","close":"expiry_close"}),on=["ticker","expiry_date"],how="left")
print(f"candidates {len(C):,}; expiry_close missing {int(C.expiry_close.isna().sum())}")
C=C.dropna(subset=["expiry_close"]).copy()

sp=C.expiry_close.values; ss=C.short_strike.values; ls=C.long_strike.values
bp=C.spread_type.eq("bull_put").values
win=np.where(bp, sp>ss, sp<ss); loss=np.where(bp, sp<=ls, sp>=ls)
C["outcome"]=np.where(win,"WIN",np.where(loss,"LOSS","PARTIAL"))
C["win"]="OOT"
C["short_delta"]=np.where(bp,-C.dfit_short,C.dfit_short)
C["p"]=np.nan; C["q"]=np.nan; C["ro"]=np.nan; C["EV"]=0.0   # recomputed by prepare()
C["D"]=np.nan
for c in F.columns:
    if c not in C.columns: C[c]=np.nan
C=C[F.columns]
OUT=pd.concat([F,C],ignore_index=True)
OUT.to_parquet(f"{ROOT}/research/dkl_2026_09_13/featATM7.parquet")
print(f"\nfeatATM7: {len(OUT):,} rows ({len(C):,} new), entries through {OUT.entry_date.max().date()}")
print(C.groupby(C.entry_date.dt.strftime('%m-%d')).size().to_string())
