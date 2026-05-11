"""
Step 2 — 1-D bucket summary across all defined dimensions.
Output: output/bucket_summary.csv (long format).
"""
import math
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _helpers import (
    load_trades,
    load_vix_daily,
    spy_monday_gap_pct,
    per_ticker_iv_pctile,
    wilson_ci,
    OUTPUT_DIR,
)

OUT_CSV = os.path.join(OUTPUT_DIR, "bucket_summary.csv")
N_FLOOR = 15


# ── Bucket assignments ───────────────────────────────────────────────────
def assign_short_delta(df):
    bins   = [0.35, 0.40, 0.45, 0.50, 0.55]
    labels = ["0.35-0.40", "0.40-0.45", "0.45-0.50", "0.50-0.55"]
    return pd.cut(df["short_delta"], bins=bins, labels=labels,
                  include_lowest=True, right=False)


def assign_quartile(series, prefix=""):
    """Quartile labels Q1..Q4 by ascending value, plus return cutpoints."""
    qs    = series.quantile([0.0, 0.25, 0.50, 0.75, 1.0])
    edges = qs.values
    labels = [f"{prefix}Q1", f"{prefix}Q2", f"{prefix}Q3", f"{prefix}Q4"]
    cats = pd.cut(series, bins=edges, labels=labels, include_lowest=True, duplicates="drop")
    return cats, edges


def assign_vix_regime(vix_close):
    bins   = [-np.inf, 15, 20, 25, np.inf]
    labels = ["VIX<15", "15-20", "20-25", "VIX>=25"]
    return pd.cut(vix_close, bins=bins, labels=labels, right=False)


def assign_iv_pctile(pctile):
    bins   = [-0.0001, 0.25, 0.50, 0.75, 1.001]
    labels = ["IVpct Q1", "IVpct Q2", "IVpct Q3", "IVpct Q4"]
    return pd.cut(pctile, bins=bins, labels=labels)


def assign_gap(gap_pct):
    bins   = [-np.inf, -0.01, -0.0025, 0.0025, 0.01, np.inf]
    labels = ["gap<-1%", "-1 to -0.25%", "-0.25 to 0.25%", "0.25 to 1%", "gap>1%"]
    return pd.cut(gap_pct, bins=bins, labels=labels)


# ── Per-bucket metrics ───────────────────────────────────────────────────
def bucket_metrics(sub: pd.DataFrame) -> dict:
    n = len(sub)
    if n == 0:
        return None
    n_win    = int(sub["is_win"].sum())
    n_loss   = int(sub["is_loss"].sum())
    n_part   = int(sub["is_partial"].sum())
    win_lo, win_hi = wilson_ci(n_win, n)
    avg_pnl_pc   = float(sub["pnl_per_contract"].mean())
    avg_dol      = float(sub["dollar_pnl"].mean())
    med_dol      = float(sub["dollar_pnl"].median())
    std_dol      = float(sub["dollar_pnl"].std(ddof=1)) if n > 1 else 0.0
    total_dol    = float(sub["dollar_pnl"].sum())
    sharpe       = (avg_dol / std_dol * math.sqrt(52)) if std_dol > 0 else float("nan")
    return dict(
        n=n,
        win_rate=n_win / n,
        win_lo=win_lo, win_hi=win_hi,
        partial_rate=n_part / n,
        loss_rate=n_loss / n,
        avg_pnl_per_contract=avg_pnl_pc,
        avg_dollar_pnl=avg_dol,
        median_dollar_pnl=med_dol,
        std_dollar_pnl=std_dol,
        sharpe_of_bucket_approx=sharpe,
        total_dollar_pnl=total_dol,
    )


def summarize_bucket(df, dim_name, bucket_col):
    rows = []
    for label, sub in df.groupby(bucket_col, observed=True):
        if len(sub) < N_FLOOR:
            rows.append(dict(
                bucket_dim=dim_name, bucket_label=str(label),
                n=len(sub),
                note=f"insufficient sample (n<{N_FLOOR})",
            ))
            continue
        m = bucket_metrics(sub)
        rows.append(dict(bucket_dim=dim_name, bucket_label=str(label), **m, note=""))
    return rows


def main():
    trades = load_trades()
    n_trades = len(trades)
    print(f"Loaded {n_trades:,} trades")

    # ── Attach VIX close on entry_date ────────────────────────────────
    vix = load_vix_daily()[["Date", "Close"]].rename(
        columns={"Date": "entry_date", "Close": "vix_close"}
    )
    trades = pd.merge_asof(
        trades.sort_values("entry_date"),
        vix.sort_values("entry_date"),
        on="entry_date", direction="backward",
        tolerance=pd.Timedelta(days=4),
    )

    # ── Attach SPY gap_pct on entry_date ─────────────────────────────
    gap = spy_monday_gap_pct(trades["entry_date"])
    trades["gap_pct"] = trades["entry_date"].map(gap)

    # ── Attach per-ticker IV percentile ──────────────────────────────
    iv = per_ticker_iv_pctile(window_weeks=52, winsor_q=0.99)
    trades = trades.merge(
        iv.rename(columns={"Symbol": "ticker", "DataDate": "entry_date"}),
        on=["ticker", "entry_date"], how="left"
    )
    print(f"VIX coverage:        {trades['vix_close'].notna().sum():,}/{n_trades}")
    print(f"gap_pct coverage:    {trades['gap_pct'].notna().sum():,}/{n_trades}")
    print(f"iv_pctile coverage:  {trades['iv_pctile'].notna().sum():,}/{n_trades}")

    # ── Apply bucket assignments ─────────────────────────────────────
    trades["bk_short_delta"]  = assign_short_delta(trades)

    trades["bk_credit_q"], cr_edges = assign_quartile(trades["credit_ratio"], "CR ")
    trades["bk_theta_q"],  th_edges = assign_quartile(trades["theta_credit_ratio"], "θ/c ")
    trades["bk_p_q"],      p_edges  = assign_quartile(trades["p"],   "p ")
    trades["bk_G_q"],      g_edges  = assign_quartile(trades["G"],   "G ")
    trades["bk_DKL_q"],    dkl_edges= assign_quartile(trades["DKL"], "DKL ")

    trades["bk_vix"]      = assign_vix_regime(trades["vix_close"])
    trades["bk_iv_pctile"]= assign_iv_pctile(trades["iv_pctile"])
    trades["bk_gap"]      = assign_gap(trades["gap_pct"])

    # ── Run summaries ────────────────────────────────────────────────
    rows = []
    rows += summarize_bucket(trades,                       "short_delta",       "bk_short_delta")
    rows += summarize_bucket(trades,                       "credit_ratio_q",    "bk_credit_q")
    rows += summarize_bucket(trades,                       "theta_credit_q",    "bk_theta_q")
    rows += summarize_bucket(trades,                       "p_q (predicted)",   "bk_p_q")
    rows += summarize_bucket(trades,                       "G_q (predicted)",   "bk_G_q")
    rows += summarize_bucket(trades,                       "DKL_q",             "bk_DKL_q")
    rows += summarize_bucket(trades,                       "spread_type",       "spread_type")
    rows += summarize_bucket(trades.dropna(subset=["bk_vix"]),       "vix_regime",   "bk_vix")
    rows += summarize_bucket(trades.dropna(subset=["bk_iv_pctile"]), "iv_pctile_q",  "bk_iv_pctile")
    rows += summarize_bucket(trades.dropna(subset=["bk_gap"]),       "spy_gap",      "bk_gap")

    out = pd.DataFrame(rows)
    # Ordering of columns
    col_order = ["bucket_dim", "bucket_label", "n",
                 "win_rate", "win_lo", "win_hi",
                 "partial_rate", "loss_rate",
                 "avg_pnl_per_contract", "avg_dollar_pnl", "median_dollar_pnl",
                 "std_dollar_pnl", "sharpe_of_bucket_approx", "total_dollar_pnl",
                 "note"]
    for c in col_order:
        if c not in out.columns:
            out[c] = np.nan
    out = out[col_order]

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out.to_csv(OUT_CSV, index=False, float_format="%.4f")
    print(f"\nWrote {OUT_CSV}  ({len(out)} rows)")

    # ── Cutpoints, printed ───────────────────────────────────────────
    def _show_edges(name, edges):
        print(f"  {name}: " + ", ".join(f"{e:.4f}" for e in edges))
    print("\nQuartile cutpoints:")
    _show_edges("credit_ratio       ", cr_edges)
    _show_edges("theta_credit_ratio ", th_edges)
    _show_edges("p                  ", p_edges)
    _show_edges("G                  ", g_edges)
    _show_edges("DKL                ", dkl_edges)


if __name__ == "__main__":
    main()
