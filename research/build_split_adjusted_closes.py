"""Write a SPLIT-ADJUSTED copy of the close store for P_real (user 2026-10-01).

output/daily_closes.parquet holds the vendor's raw end-of-day price, so a split prints as a single
huge "return" (GE +696% on 2021-08-03 = its 1:8 reverse split). P_real demeans its 252-session window,
so one such bar shifts every other return and corrupts p/q/ro for ~a year after the event (handoff §0.69k).

This writes output/daily_closes_split_adj.parquet. The RAW store is never modified: build_frame.py
settles trades against it, and those strikes are on the as-of-date scale, so settlement must stay raw.
Only P_real / gap-drift / sigma read the adjusted copy (bear_regime_sweep.prepare).

Each Yahoo event is VERIFIED against the store before anything is changed: the observed session return
must match the split's implied 1/R - 1 within TOL. Events that do not match are reported and skipped --
a spinoff factor applied to a series that never gapped would invent a jump instead of removing one.

    python3 research/build_split_adjusted_closes.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
RAW = ROOT / "output/daily_closes.parquet"
SPLITS = ROOT / "output/yahoo_split_history.csv"
OUT = ROOT / "output/daily_closes_split_adj.parquet"
TOL = 0.05          # absolute return units between observed and implied
SEARCH = 3          # sessions either side of the Yahoo date


def main() -> None:
    cl = pd.read_parquet(RAW); cl["date"] = pd.to_datetime(cl.date).dt.normalize()
    cl = cl.dropna(subset=["close"]).drop_duplicates(["ticker", "date"]).sort_values(["ticker", "date"]).reset_index(drop=True)
    sp = pd.read_csv(SPLITS, parse_dates=["SplitDate"])
    out = cl.copy(); applied = []; skipped = []
    for tk, g in cl.groupby("ticker"):
        ev = sp[sp.Symbol == tk]
        if ev.empty:
            continue
        idx = g.index.to_numpy(); c = g.close.to_numpy(float); d = g.date.to_numpy("datetime64[ns]")
        r = np.concatenate([[np.nan], c[1:] / c[:-1] - 1.0])
        factor = np.ones(len(c))
        for e in ev.itertuples():
            R = float(e.Ratio) if e.Ratio else np.nan
            if not np.isfinite(R) or R <= 0:
                skipped.append((tk, e.SplitDate.date(), R, None, "bad ratio")); continue
            implied = 1.0 / R - 1.0
            near = np.where(np.abs((d - np.datetime64(e.SplitDate)).astype("timedelta64[D]").astype(int)) <= SEARCH)[0]
            near = near[near > 0]
            if len(near) == 0:
                skipped.append((tk, e.SplitDate.date(), R, None, "no sessions near the date")); continue
            j = near[np.nanargmin(np.abs(r[near] - implied))]
            if not np.isfinite(r[j]) or abs(r[j] - implied) > TOL:
                skipped.append((tk, e.SplitDate.date(), R, r[j], "store shows no matching gap")); continue
            factor[:j] *= 1.0 / R
            applied.append((tk, e.SplitDate.date(), pd.Timestamp(d[j]).date(), R, implied, r[j], j))
        if (factor != 1.0).any():
            out.loc[idx, "close"] = c * factor
    OUT.parent.mkdir(parents=True, exist_ok=True); out.to_parquet(OUT)
    print(f"applied {len(applied)} splits, skipped {len(skipped)}")
    A = pd.DataFrame(applied, columns=["ticker", "yahoo_date", "store_date", "ratio", "implied_ret", "observed_ret", "idx"])
    print("\nAPPLIED (back-adjusted: every close before store_date multiplied by 1/ratio)")
    print(A[["ticker", "yahoo_date", "store_date", "ratio", "implied_ret", "observed_ret"]].to_string(index=False, float_format=lambda x: f"{x:.4g}"))
    S = pd.DataFrame(skipped, columns=["ticker", "yahoo_date", "ratio", "observed_ret", "why"])
    log = pd.concat([A.assign(applied=True, why="verified against the store")[["ticker", "yahoo_date", "ratio", "applied", "why"]],
                     S.assign(applied=False)[["ticker", "yahoo_date", "ratio", "applied", "why"]]], ignore_index=True)
    log.rename(columns={"ticker": "Symbol", "yahoo_date": "SplitDate"}).sort_values(["Symbol", "SplitDate"]).to_csv(ROOT / "output/split_adjustment_log.csv", index=False)
    print("\nSKIPPED (left untouched)"); print(S.to_string(index=False, float_format=lambda x: f"{x:.4g}"))
    # verification: biggest single-session move per ticker, before vs after
    def worst(df):
        rows = []
        for tk, g in df.groupby("ticker"):
            c = g.sort_values("date").close.to_numpy(float)
            if len(c) < 2: continue
            rr = c[1:] / c[:-1] - 1.0; rows.append((tk, float(np.nanmax(np.abs(rr)))))
        return pd.DataFrame(rows, columns=["ticker", "max_abs_ret"]).set_index("ticker")
    b, a = worst(cl), worst(out); cmp = b.join(a, lsuffix="_raw", rsuffix="_adj")
    bad = cmp[(cmp.max_abs_ret_raw > 0.5) | (cmp.max_abs_ret_adj > 0.5)]
    print("\nVERIFY: names with a >50% single session, raw vs adjusted")
    print((100 * bad).round(1).to_string())
    print(f"\nnames still over 50% after adjustment: {int((cmp.max_abs_ret_adj > 0.5).sum())}")
    print(f"wrote {OUT}: {len(out):,} rows, {out.ticker.nunique()} tickers (raw store untouched)")


if __name__ == "__main__":
    main()
