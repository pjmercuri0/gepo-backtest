"""Score a chain snapshot (single timestamp) into the canonical candidate
pool. Emits one row per surviving SP100 spread with: filters, p/r₀/q,
Kelly EV, DKL, Γᵢ, raw bid/ask of each leg, and the bid/ask half-spread
cost of crossing both legs (a static snapshot measurement, NOT drift —
drift requires two snapshots, see drift_compare.py).

Outputs:
   live/data/scored/YYYY-MM-DD/HHMM.parquet  (canonical scored output, joinable)
   live/data/scored/YYYY-MM-DD/HHMM.csv      (same data, human-readable)
   live/data/scored/YYYY-MM-DD/HHMM.html     (top picks + summary)

Input schemas accepted:
   - Discount-Greek `DG_YYYYMMDD.zip` (current vendor)
   - OPRA-style parquet/csv with columns: Symbol, ExpirationDate, StrikePrice,
     PutCall, BidPrice, AskPrice, OpenInterest, ImpliedVolatility, Delta,
     UnderlyingPrice, DataDate, plus optional Theta/Gamma/Vega.
   The loader detects format by extension; new vendors can be added in
   `load_chain` without touching downstream.

Usage:
   python3 analyze_chain.py --in data/DG_20260512.zip
   python3 analyze_chain.py --in data/opra/2026-05-13/1545.parquet --time 1545
"""
from __future__ import annotations
import argparse
import math
import os
import re
import sys
import zipfile

import numpy as np
import pandas as pd

import config
import spreads
import ground


REQUIRED_COLS = [
    "Symbol", "ExpirationDate", "StrikePrice", "PutCall",
    "BidPrice", "AskPrice", "OpenInterest",
    "ImpliedVolatility", "Delta", "UnderlyingPrice", "DataDate",
]


def load_chain(path: str) -> pd.DataFrame:
    """Dispatch to the right loader by file extension.

    Discount-Greek vendor:     DG_YYYYMMDD.zip   (two big CSVs inside)
    OPRA-shaped parquet:       *.parquet
    OPRA-shaped CSV:           *.csv
    """
    if path.endswith(".zip"):
        frames = []
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not name.endswith(".csv"):
                    continue
                print(f"   reading {name}", flush=True)
                with z.open(name) as fh:
                    frames.append(pd.read_csv(fh, low_memory=False))
        df = pd.concat(frames, ignore_index=True)
    elif path.endswith(".parquet"):
        df = pd.read_parquet(path)
    elif path.endswith(".csv"):
        df = pd.read_csv(path, low_memory=False)
    else:
        raise ValueError(f"unknown chain format: {path}")

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"chain missing required columns: {missing}")
    df["DataDate"]       = pd.to_datetime(df["DataDate"])
    df["ExpirationDate"] = pd.to_datetime(df["ExpirationDate"])
    return df


def apply_canonical_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Filter the raw chain to the SP100 universe + Greek/DTE/OI canonical
    pre-candidate filters (these are applied INSIDE spreads.build_candidates
    too, but doing them here first cuts the dataframe by 99% and saves time)."""
    n0 = len(df)
    df = df[df["Symbol"].isin(config.SP100_TICKERS)]
    df["AbsDelta"] = df["Delta"].abs()
    df = df[df["AbsDelta"].between(config.DELTA_MIN - 0.05, config.DELTA_MAX + 0.05)]
    df["DTE"] = (df["ExpirationDate"] - df["DataDate"]).dt.days
    df = df[df["DTE"].between(config.DTE_MIN, config.DTE_MAX)]
    df = df[df["OpenInterest"] >= config.MIN_OPEN_INTEREST]
    df = df[df["BidPrice"] > 0]
    df = df[df["AskPrice"] > df["BidPrice"]]
    print(f"   pre-filter:  {n0:,} rows → {len(df):,} rows", flush=True)
    return df.reset_index(drop=True)


def join_raw_quotes(cands: pd.DataFrame, chain: pd.DataFrame) -> pd.DataFrame:
    """Pull short/long leg bid/ask from the chain back onto each candidate
    row so we can compute fill drift (mid-mid vs realistic sell-bid/buy-ask)."""
    key_cols = ["Symbol", "ExpirationDate", "StrikePrice", "PutCall"]
    slim = chain[key_cols + ["BidPrice", "AskPrice"]].copy()

    short_pc = np.where(cands["spread_type"] == "bear_call", "call", "put")
    long_pc  = short_pc.copy()
    short_join = pd.DataFrame({
        "Symbol":         cands["ticker"],
        "ExpirationDate": pd.to_datetime(cands["expiry_date"]),
        "StrikePrice":    cands["short_strike"],
        "PutCall":        short_pc,
    })
    long_join = pd.DataFrame({
        "Symbol":         cands["ticker"],
        "ExpirationDate": pd.to_datetime(cands["expiry_date"]),
        "StrikePrice":    cands["long_strike"],
        "PutCall":        long_pc,
    })

    short_q = short_join.merge(slim, on=key_cols, how="left") \
                        .rename(columns={"BidPrice": "short_bid", "AskPrice": "short_ask"})
    long_q  = long_join.merge(slim, on=key_cols, how="left") \
                       .rename(columns={"BidPrice": "long_bid",  "AskPrice": "long_ask"})

    out = cands.copy().reset_index(drop=True)
    out["short_bid"] = short_q["short_bid"].values
    out["short_ask"] = short_q["short_ask"].values
    out["long_bid"]  = long_q["long_bid"].values
    out["long_ask"]  = long_q["long_ask"].values

    # Worst-case execution at this snapshot's bid/ask:
    #   sell short leg at its BID, buy long leg at its ASK
    # ba_halfspread_cost = mid_credit − cross_bidask_credit
    #                    = half-spread of each leg summed
    # This is a STATIC measurement — it tells you the cost of crossing both
    # bid/asks at this snapshot timestamp. It is NOT drift. True drift
    # requires comparing two snapshots and is computed in drift_compare.py.
    out["cross_bidask_credit"] = out["short_bid"] - out["long_ask"]
    out["mid_credit"]          = out["net_credit"]
    out["ba_halfspread_cost"]  = out["mid_credit"] - out["cross_bidask_credit"]
    out["ba_halfspread_pct"]   = np.where(out["mid_credit"] > 0,
                                           out["ba_halfspread_cost"] / out["mid_credit"] * 100,
                                           np.nan)
    return out


def render_html(df_top: pd.DataFrame, df_full: pd.DataFrame, date_str: str, time_str: str) -> str:
    n  = len(df_full)
    nb = (df_full["spread_type"] == "bear_call").sum()
    nu = (df_full["spread_type"] == "bull_put").sum()
    ba_med  = df_full["ba_halfspread_cost"].median()
    ba_pct  = df_full["ba_halfspread_pct"].median()
    untradeable = (df_full["cross_bidask_credit"] <= 0).sum()
    top_med_ba  = df_top["ba_halfspread_pct"].median()

    head_rows = []
    for _, r in df_top.iterrows():
        head_rows.append(
            f"<tr><td>{r['ticker']}</td>"
            f"<td>{r['spread_type'].replace('_', ' ')}</td>"
            f"<td>${r['short_strike']:.2f} / ${r['long_strike']:.2f}</td>"
            f"<td>{r['DTE']:.0f}d</td>"
            f"<td>{r['net_credit']/r['max_loss']:.2f}</td>"
            f"<td>${r['mid_credit']:.3f}</td>"
            f"<td>${r['cross_bidask_credit']:.3f}</td>"
            f"<td class='{'neg' if r['ba_halfspread_pct']>50 else 'warn' if r['ba_halfspread_pct']>25 else ''}'>"
            f"{r['ba_halfspread_pct']:.1f}%</td>"
            f"<td>{r['GROUND']*100:+.2f}%</td>"
            f"</tr>"
        )
    head_rows = "\n".join(head_rows)

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>chain analysis {date_str} {time_str}</title>
<style>
body {{ background:#0f0f0f; color:#e8e8e8; font:14px/1.4 -apple-system,sans-serif;
       max-width:1200px; margin:24px auto; padding:0 16px; }}
h1, h2 {{ color:#fff; font-weight:600; }}
.subtitle {{ color:#888780; font-size:13px; margin-bottom:24px; }}
.stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px;
          background:#1a1a1a; border:1px solid #333; padding:16px;
          border-radius:6px; margin-bottom:24px; }}
.stat-label {{ color:#888780; font-size:11px; text-transform:uppercase; }}
.stat-value {{ color:#fff; font-size:20px; font-weight:600; margin-top:4px; }}
table {{ border-collapse:collapse; width:100%; margin-bottom:24px; }}
th, td {{ padding:8px 12px; text-align:left; border-bottom:1px solid #2a2a2a; }}
th {{ color:#888780; font-size:11px; text-transform:uppercase; font-weight:500; }}
td {{ font-variant-numeric:tabular-nums; }}
.neg {{ color:#E24B4A; }}
.warn {{ color:#EF9F27; }}
.pos {{ color:#1D9E75; }}
.note {{ color:#888780; font-size:12px; margin-top:8px; }}
</style></head><body>

<h1>chain snapshot · {date_str} {time_str}</h1>
<div class="subtitle">canonical filters: SP100 / DTE {config.DTE_MIN}-{config.DTE_MAX} / |Δ| {config.DELTA_MIN}-{config.DELTA_MAX}
/ both-leg OI ≥ {config.MIN_OPEN_INTEREST} / b ≤ {config.BACKTEST_MAX_CREDIT_RATIO} (paper)</div>

<h2>summary</h2>
<div class="stats">
  <div class="stat"><div class="stat-label">candidates</div><div class="stat-value">{n:,}</div></div>
  <div class="stat"><div class="stat-label">bear-call / bull-put</div><div class="stat-value">{nb} / {nu}</div></div>
  <div class="stat"><div class="stat-label">median b/a half-spread cost</div><div class="stat-value">${ba_med:.3f}<br><span style="font-size:13px;color:#888780">({ba_pct:.1f}% of mid credit)</span></div></div>
  <div class="stat"><div class="stat-label">untradeable at b/a</div><div class="stat-value">{untradeable}<br><span style="font-size:13px;color:#888780">sell_bid ≤ buy_ask of other leg</span></div></div>
</div>

<div class="note">ba_halfspread_cost = (mid_credit) − (short_bid − long_ask) = sum of both legs'
half-spreads. <b>This is a static measure of bid/ask width at one snapshot, NOT drift.</b>
True drift requires comparing two snapshots — see <code>drift_compare.py</code>.</div>

<h2>top 5 picks (by Γᵢ rank)</h2>
<table>
<tr><th>ticker</th><th>direction</th><th>strikes</th><th>DTE</th><th>b</th>
    <th>mid credit</th><th>cross-b/a credit</th><th>b/a half-sp %</th><th>Γᵢ</th></tr>
{head_rows}
</table>

<div class="note">top-5 median b/a half-spread cost: <b>{top_med_ba:.1f}% of mid credit</b>.
   Lower is better. This number is the floor on transaction cost if you must cross b/a;
   patient limit-order trading can do better.</div>

</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in",  dest="input_path", required=True,
                    help="chain file: DG_*.zip, *.parquet, or *.csv")
    ap.add_argument("--time", default="1600",
                    help="snapshot time HHMM (default 1600 = EOD close)")
    ap.add_argument("--top-n", type=int, default=5)
    ap.add_argument("--output-root", default="live/data/scored")
    args = ap.parse_args()

    # Canonical config: paper backtest cap (b ≤ 5)
    config.MAX_CREDIT_RATIO = config.BACKTEST_MAX_CREDIT_RATIO

    # Disable regime filter for the chain analysis (we want all candidates, not direction-gated)
    spreads.REGIME_FILTER          = False
    spreads.GAP_FILTER             = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    ground.RANKING_MODE            = "GROUND"
    ground.DKL_K                   = 1.0

    print(f"\n== scoring {args.input_path} ==\n", flush=True)
    chain = load_chain(args.input_path)
    data_date = chain["DataDate"].iloc[0]
    date_str  = data_date.strftime("%Y-%m-%d")
    time_str  = str(args.time).zfill(4)
    out_dir   = os.path.join(args.output_root, date_str)
    os.makedirs(out_dir, exist_ok=True)
    print(f"   DataDate: {date_str}   snapshot time: {time_str}", flush=True)

    chain = apply_canonical_filters(chain)

    print(f"   building candidates...", flush=True)
    cands = spreads.build_candidates(chain)
    print(f"   {len(cands):,} candidate spreads survived all filters", flush=True)

    print(f"   scoring with Γᵢ...", flush=True)
    # score_candidates fills p/q/ro/G/DKL/EV/w_star per row
    scored = ground.score_candidates(cands)
    # Apply the per-week GROUND computation (handles the RANKING_MODE switch)
    scored["entry_date"] = pd.to_datetime(scored["entry_date"])
    scored_groups = []
    for _, week_df in scored.groupby("entry_date", sort=False):
        scored_groups.append(ground._compute_ground_for_week(week_df))
    scored = pd.concat(scored_groups, ignore_index=True)
    scored = scored.dropna(subset=["GROUND"]).reset_index(drop=True)
    print(f"   {len(scored):,} candidates with valid Γᵢ", flush=True)

    print(f"   joining raw bid/ask for fill-drift calc...", flush=True)
    scored = join_raw_quotes(scored, chain)
    scored["DTE"] = scored["DTE"].astype(int) if "DTE" in scored.columns else \
                    ((pd.to_datetime(scored["expiry_date"]) -
                      pd.to_datetime(scored["entry_date"])).dt.days)
    scored = scored.sort_values("GROUND", ascending=False).reset_index(drop=True)

    cols_out = ["ticker", "spread_type", "expiry_date", "DTE",
                "short_strike", "long_strike", "p", "ro", "q", "w_star",
                "G", "EV", "DKL", "GROUND",
                "mid_credit", "cross_bidask_credit", "ba_halfspread_cost", "ba_halfspread_pct",
                "short_bid", "short_ask", "long_bid", "long_ask"]
    out_parquet = os.path.join(out_dir, f"{time_str}.parquet")
    out_csv     = os.path.join(out_dir, f"{time_str}.csv")
    out_html    = os.path.join(out_dir, f"{time_str}.html")

    scored[cols_out].to_parquet(out_parquet, compression="zstd")
    scored[cols_out].to_csv(out_csv, index=False)
    print(f"   wrote {out_parquet}  ({len(scored):,} rows)", flush=True)
    print(f"   wrote {out_csv}", flush=True)

    top_n = min(args.top_n, len(scored))
    with open(out_html, "w") as f:
        f.write(render_html(scored.head(top_n), scored, date_str, time_str))
    print(f"   wrote {out_html}", flush=True)

    # Quick stdout report
    print(f"\n   {len(scored):,} candidates  ·  "
          f"median b/a half-spread cost: ${scored['ba_halfspread_cost'].median():.3f} "
          f"({scored['ba_halfspread_pct'].median():.1f}% of mid)")
    print(f"   top-{top_n} median b/a half-spread cost: {scored.head(top_n)['ba_halfspread_pct'].median():.1f}% of mid")
    n_untradeable = (scored['cross_bidask_credit'] <= 0).sum()
    print(f"   {n_untradeable:,} / {len(scored):,} ({n_untradeable/len(scored)*100:.1f}%) "
          f"untradeable at bid/ask")


if __name__ == "__main__":
    sys.exit(main())
