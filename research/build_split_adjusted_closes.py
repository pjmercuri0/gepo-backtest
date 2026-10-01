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
import ent_canon as ec
RAW = ROOT / "output/daily_closes.parquet"
SPLITS = ROOT / "output/yahoo_split_history.csv"
OUT = ROOT / "output/daily_closes_split_adj.parquet"
SEARCH = 3          # sessions either side of the Yahoo date


def main() -> None:
    cl = pd.read_parquet(RAW); cl["date"] = pd.to_datetime(cl.date).dt.normalize()
    cl = cl.dropna(subset=["close"]).drop_duplicates(["ticker", "date"]).sort_values(["ticker", "date"]).reset_index(drop=True)
    sp = pd.read_csv(SPLITS, parse_dates=["SplitDate"])
    out, log = ec.apply_split_adjustment(cl, str(SPLITS), search=SEARCH)   # tolerance/floor live in ent_canon
    applied = [(r.Symbol, r.SplitDate.date(), r.ratio, r.why) for r in log[log.applied].itertuples()]
    skipped = [(r.Symbol, r.SplitDate.date(), r.ratio, r.why) for r in log[~log.applied].itertuples()]
    log.to_csv(ROOT / "output/split_adjustment_log.csv", index=False)
    OUT.parent.mkdir(parents=True, exist_ok=True); out.to_parquet(OUT)
    print(f"applied {len(applied)} splits, skipped {len(skipped)}")
    A = pd.DataFrame(applied, columns=["ticker", "yahoo_date", "ratio", "why"])
    print("\nAPPLIED (back-adjusted: every close before the gap multiplied by 1/ratio)"); print(A.to_string(index=False))
    S = pd.DataFrame(skipped, columns=["ticker", "yahoo_date", "ratio", "why"])
    print("\nSKIPPED (left untouched)"); print(S.to_string(index=False))
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
