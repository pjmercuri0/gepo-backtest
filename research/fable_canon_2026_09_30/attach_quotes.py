"""Attach vendor leg quotes (bid/ask mid) and the short-leg IV to every featATM8 candidate.

The frame stores net_credit == model_credit; the Fable Canon needs the QUOTED mid
(quote gate, rank key) and the short leg's vendor IV (bear sleeve filter).
Output: research/fable_canon_2026_09_30/frame_quotes.parquet (keys + vendor_mid, IV, leg bid/ask).
"""
import sys, time
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
import ent_canon as ec

FRAME = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "research/dkl_2026_09_13/featATM8.parquet"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "research/fable_canon_2026_09_30/frame_quotes.parquet"
KEY = ["ticker", "entry_date", "expiry_date", "spread_type", "short_strike", "long_strike"]
COLS = ["Symbol", "DataDate", "ExpirationDate", "PutCall", "StrikePrice", "BidPrice", "AskPrice", "ImpliedVolatility"]

f = pd.read_parquet(FRAME, columns=KEY)
f["entry_date"] = pd.to_datetime(f.entry_date).dt.normalize(); f["expiry_date"] = pd.to_datetime(f.expiry_date).dt.normalize()
parts = []
for y in range(2020, 2027):
    t0 = time.time()
    ch = pd.read_parquet(ROOT / ec.vendor_year_parquet(y), columns=COLS)
    ch["DataDate"] = pd.to_datetime(ch.DataDate).dt.normalize(); ch["ExpirationDate"] = pd.to_datetime(ch.ExpirationDate).dt.normalize()
    ch["PutCall"] = ch.PutCall.astype(str).str.lower().str.strip()
    for c in ("StrikePrice", "BidPrice", "AskPrice", "ImpliedVolatility"):
        ch[c] = pd.to_numeric(ch[c], errors="coerce")
    ch = ch.rename(columns={"Symbol": "ticker", "DataDate": "entry_date", "ExpirationDate": "expiry_date"})
    fy = f[f.entry_date.dt.year == y]
    ch = ch[ch.ticker.isin(set(fy.ticker)) & ch.entry_date.isin(set(fy.entry_date))]
    fy = fy.assign(PutCall=np.where(fy.spread_type.eq("bull_put"), "put", "call"))
    m = fy.merge(ch.rename(columns={"StrikePrice": "short_strike", "BidPrice": "s_bid", "AskPrice": "s_ask", "ImpliedVolatility": "IV"}),
                 on=["ticker", "entry_date", "expiry_date", "PutCall", "short_strike"], how="left")
    m = m.merge(ch.rename(columns={"StrikePrice": "long_strike", "BidPrice": "l_bid", "AskPrice": "l_ask", "ImpliedVolatility": "long_IV"}),
                on=["ticker", "entry_date", "expiry_date", "PutCall", "long_strike"], how="left")
    m["vendor_mid"] = ((m.s_bid + m.s_ask) / 2 - (m.l_bid + m.l_ask) / 2).round(4)
    m["vendor_nat"] = (m.s_bid - m.l_ask).round(4)     # natural (sell short at bid, buy long at ask)
    dup = m.duplicated(KEY).sum()
    print(f"{y}: {len(fy):,} cands, matched mid {m.vendor_mid.notna().mean():.3%}, IV {m.IV.notna().mean():.3%}, dup keys {dup} [{time.time()-t0:.0f}s]", flush=True)
    parts.append(m.drop_duplicates(KEY))
out = pd.concat(parts, ignore_index=True).drop(columns=["PutCall"])
out.to_parquet(OUT); print("wrote", OUT, len(out))
