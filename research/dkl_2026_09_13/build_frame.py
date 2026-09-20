"""Rebuild the ENTIRE candidate frame from the vendor year files.

Replaces featATM6.parquet, whose builders no longer exist: its in-sample half came from
/tmp/gepo_pairs.parquet and /tmp/gepo_expclose.parquet, both long gone, so 86% of the
published book had no reproduction path (audit 2026-09-19, weakness #4).

Rules recovered from band_checks.build():
    short leg fitted delta in [0.50, 0.60], the one nearest 0.55
    long leg  = adjacent strike on the same right
    model_credit > 0.01, max_loss > 0, Friday expiries, DTE 1-4
    one row per (ticker, entry_date, expiry_date, spread_type)
    liquidity: OpenInterest >= config.MIN_OPEN_INTEREST on both legs
    width <= config.MAX_SPREAD_WIDTH (2.5)

Universe is the FULL config.SP100_TICKERS (audit weakness #5).  featATM6 silently used a
narrower 80-name pool inherited from oot_pairs_q.parquet; that file has no builder either
and the restriction was never a deliberate rule.

Validated 2026-09-19: rebuilding August 2026 reproduced featATM6's rows with 0 missing,
model_credit and D_ent exact to 1e-6, and 98.8% identical strikes.

    python3 research/dkl_2026_09_13/build_frame.py [--out featATM8.parquet]
"""
from __future__ import annotations
import sys, argparse
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import ent_canon as ec, config as cfg

LO, HI, TGT = 0.50, 0.60, 0.55
COLS = ["Symbol","DataDate","ExpirationDate","PutCall","StrikePrice","BidPrice","AskPrice",
        "LastPrice","ImpliedVolatility","UnderlyingPrice","Delta","Gamma","Vega","Theta","OpenInterest"]


def build_year(year: int, pool: set, min_oi: int) -> pd.DataFrame:
    path = ROOT / ec.vendor_year_parquet(year)
    if not path.exists():
        print(f"  {year}: MISSING {path.name}"); return pd.DataFrame()
    ch = pd.read_parquet(path, columns=COLS)
    ch["DataDate"] = pd.to_datetime(ch.DataDate).dt.normalize()
    ch["ExpirationDate"] = pd.to_datetime(ch.ExpirationDate).dt.normalize()
    ch = ch[ch.Symbol.isin(pool)].copy()
    ch["DTE"] = (ch.ExpirationDate - ch.DataDate).dt.days
    ch = ch[ch.DTE.between(1, 4) & (ch.ExpirationDate.dt.dayofweek == 4)]
    if ch.empty:
        print(f"  {year}: no 1-4 DTE Friday rows"); return pd.DataFrame()
    for c in COLS[4:]:
        ch[c] = pd.to_numeric(ch[c], errors="coerce")
    ch["PutCall"] = ch.PutCall.astype(str).str.lower().str.strip()
    fits = ec.fit_smiles(ch)
    # Vectorized adjacent-strike pairing (the band_checks.py pattern): sort by strike within
    # (chain, right), shift +-1 for the neighbour, no Python loop over chains.
    q = ch[(ch.BidPrice > 0) & (ch.AskPrice > ch.BidPrice) & (ch.OpenInterest.fillna(0) >= min_oi)]
    q = q.sort_values(["Symbol", "DataDate", "ExpirationDate", "PutCall", "StrikePrice"])
    g = q.groupby(["Symbol", "DataDate", "ExpirationDate", "PutCall"], sort=False)["StrikePrice"]
    q = q.assign(lo=g.shift(1), hi=g.shift(-1))
    put = q[q.PutCall.eq("put")].dropna(subset=["lo"]).assign(spread_type="bull_put", long_strike=lambda d: d.lo)
    cal = q[q.PutCall.eq("call")].dropna(subset=["hi"]).assign(spread_type="bear_call", long_strike=lambda d: d.hi)
    cand = (pd.concat([put, cal], ignore_index=True)
              .rename(columns={"Symbol": "ticker", "DataDate": "entry_date", "ExpirationDate": "expiry_date",
                               "StrikePrice": "short_strike", "UnderlyingPrice": "entry_price"})
              [["ticker", "entry_date", "expiry_date", "DTE", "spread_type", "entry_price", "short_strike", "long_strike"]])
    cand["short_strike"] = cand.short_strike.astype(float); cand["long_strike"] = cand.long_strike.astype(float)
    cand["width"] = (cand.short_strike - cand.long_strike).abs().round(4)
    cand = cand[cand.width <= cfg.MAX_SPREAD_WIDTH + 1e-9]
    if cand.empty:
        return pd.DataFrame()
    P = ec.price_spreads(cand, fits)
    b = P[P.dfit_short.between(LO, HI) & (P.dfit_long < P.dfit_short) & (P.model_credit > 0.01)].copy()
    b["dist"] = (b.dfit_short - TGT).abs()
    C = b.loc[b.groupby(["ticker","entry_date","expiry_date","spread_type"], sort=False)["dist"].idxmin()].copy()
    C["net_credit"] = C.model_credit
    C["max_loss"] = (C.width - C.model_credit).round(4)
    C = C[C.max_loss > 0].copy()
    print(f"  {year}: {len(ch):>9,} chain rows -> {len(C):>6,} candidates  "
          f"({C.entry_date.min().date()}..{C.entry_date.max().date()}, {C.ticker.nunique()} tickers)")
    return C


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="featATM8.parquet")
    ap.add_argument("--min-oi", type=int, default=cfg.MIN_OPEN_INTEREST)
    a = ap.parse_args()
    pool = set(cfg.SP100_TICKERS)
    print(f"universe: {len(pool)} SP100 tickers; OpenInterest >= {a.min_oi}")
    parts = [build_year(y, pool, a.min_oi) for y in range(2020, 2027)]
    C = pd.concat([p for p in parts if len(p)], ignore_index=True)

    cl = pd.read_parquet(ROOT / "output/daily_closes.parquet")
    cl["date"] = pd.to_datetime(cl.date).dt.normalize()
    C = C.merge(cl.rename(columns={"date":"expiry_date","close":"expiry_close"}),
                on=["ticker","expiry_date"], how="left")
    n_unsettled = int(C.expiry_close.isna().sum())
    C = C.dropna(subset=["expiry_close"]).copy()

    sp = C.expiry_close.values; ss = C.short_strike.values; ls = C.long_strike.values
    bp = C.spread_type.eq("bull_put").values
    win = np.where(bp, sp > ss, sp < ss); loss = np.where(bp, sp <= ls, sp >= ls)
    C["outcome"] = np.where(win, "WIN", np.where(loss, "LOSS", "PARTIAL"))
    C["win"] = np.where(C.entry_date.dt.year <= 2025, "IS", "OOT")
    C["short_delta"] = np.where(bp, -C.dfit_short, C.dfit_short)
    for c in ("p","q","ro","D"):
        C[c] = np.nan
    C["EV"] = 0.0          # recomputed downstream by bear_regime_sweep.prepare()
    out = ROOT / "research/dkl_2026_09_13" / a.out
    C.to_parquet(out)
    print(f"\nwrote {out}: {len(C):,} rows, {C.entry_date.min().date()}..{C.entry_date.max().date()}, "
          f"{C.ticker.nunique()} tickers ({n_unsettled:,} dropped for unsettled expiry)")
    print(C.groupby(C.entry_date.dt.year).agg(n=("ticker","size"), tickers=("ticker","nunique")).to_string())


if __name__ == "__main__":
    main()
