"""
Step 1 — Headline validation.
Reproduce aggregate metrics from the trade log and confirm plausibility.
Read-only on the trade log.
"""
import math
import os
import sys

import numpy as np
import pandas as pd

HERE        = os.path.dirname(os.path.abspath(__file__))
TRADES_CSV  = os.path.join(HERE, "..", "output", "all_trades.csv")
OUT_TXT     = os.path.join(HERE, "output", "headline_validation.txt")


def fmt_dollar(x):
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def main():
    if not os.path.exists(TRADES_CSV):
        print(f"ERROR: trade log not found at {TRADES_CSV}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(TRADES_CSV)

    # Drop all-null diagnostic columns (per spec)
    for col in ("n_samples", "reason", "best_ground"):
        if col in df.columns:
            df = df.drop(columns=col)

    # Type fixups
    df["entry_date"]  = pd.to_datetime(df["entry_date"])
    df["expiry_date"] = pd.to_datetime(df["expiry_date"])

    # Derived columns
    df["credit_ratio"] = df["net_credit"] / df["max_loss"]
    df["is_win"]       = df["result"] == "WIN"
    df["is_loss"]      = df["result"] == "LOSS"
    df["is_partial"]   = df["result"] == "PARTIAL"

    n            = len(df)
    n_win        = int(df["is_win"].sum())
    n_loss       = int(df["is_loss"].sum())
    n_partial    = int(df["is_partial"].sum())
    win_rate     = n_win / n
    avg_pnl      = df["dollar_pnl"].mean()
    median_pnl   = df["dollar_pnl"].median()
    std_pnl      = df["dollar_pnl"].std(ddof=1)
    total_pnl    = df["dollar_pnl"].sum()

    sharpe_per_trade = avg_pnl / std_pnl if std_pnl > 0 else float("nan")
    sharpe_annual    = sharpe_per_trade * math.sqrt(52)   # ~52 weeks/yr

    cum_pnl     = df.sort_values("entry_date")["dollar_pnl"].cumsum().to_numpy()
    running_max = np.maximum.accumulate(cum_pnl)
    drawdowns   = cum_pnl - running_max
    max_dd_dol  = float(drawdowns.min())

    # Direction breakdown
    dir_counts = df["spread_type"].value_counts().to_dict()
    bp_n = dir_counts.get("bull_put", 0)
    bc_n = dir_counts.get("bear_call", 0)
    bp_df = df[df["spread_type"] == "bull_put"]
    bc_df = df[df["spread_type"] == "bear_call"]

    def _stats(sub):
        if len(sub) == 0:
            return dict(n=0, win=0, win_rate=0, avg_pnl=0, total=0)
        return dict(
            n=len(sub),
            win=int(sub["is_win"].sum()),
            win_rate=float(sub["is_win"].mean()),
            avg_pnl=float(sub["dollar_pnl"].mean()),
            total=float(sub["dollar_pnl"].sum()),
        )
    bp_stats = _stats(bp_df)
    bc_stats = _stats(bc_df)

    lines = []
    lines.append("=" * 72)
    lines.append("  STEP 1 — HEADLINE VALIDATION")
    lines.append("=" * 72)
    lines.append(f"  Trade log:       {os.path.relpath(TRADES_CSV, HERE)}")
    lines.append(f"  Date range:      {df['entry_date'].min().date()} → "
                 f"{df['entry_date'].max().date()}")
    lines.append(f"  Total trades:    {n:,}")
    lines.append("")
    lines.append("  Result distribution")
    lines.append(f"    WIN     {n_win:>6,}  ({n_win/n*100:5.1f}%)")
    lines.append(f"    LOSS    {n_loss:>6,}  ({n_loss/n*100:5.1f}%)")
    lines.append(f"    PARTIAL {n_partial:>6,}  ({n_partial/n*100:5.1f}%)")
    lines.append(f"    Win rate (W/total):       {win_rate*100:5.2f}%")
    lines.append("")
    lines.append("  P&L (per trade, $/contract × contracts × 100)")
    lines.append(f"    avg_dollar_pnl:           {fmt_dollar(avg_pnl)}")
    lines.append(f"    median_dollar_pnl:        {fmt_dollar(median_pnl)}")
    lines.append(f"    std_dollar_pnl:           {fmt_dollar(std_pnl)}")
    lines.append(f"    total_dollar_pnl:         {fmt_dollar(total_pnl)}")
    lines.append("")
    lines.append("  Risk")
    lines.append(f"    sharpe_per_trade:         {sharpe_per_trade:.3f}")
    lines.append(f"    sharpe_annualised (≈52):  {sharpe_annual:.3f}  (approximate; "
                 "weekly cohorts, not weekly returns)")
    lines.append(f"    max_drawdown ($, cum):    {fmt_dollar(max_dd_dol)}")
    lines.append("")
    lines.append("  Direction breakdown")
    lines.append(f"    bull_put     n={bp_stats['n']:>5,}  win_rate={bp_stats['win_rate']*100:5.2f}%  "
                 f"avg_pnl={fmt_dollar(bp_stats['avg_pnl'])}  total={fmt_dollar(bp_stats['total'])}")
    lines.append(f"    bear_call    n={bc_stats['n']:>5,}  win_rate={bc_stats['win_rate']*100:5.2f}%  "
                 f"avg_pnl={fmt_dollar(bc_stats['avg_pnl'])}  total={fmt_dollar(bc_stats['total'])}")
    lines.append("=" * 72)

    text = "\n".join(lines)
    print(text)
    os.makedirs(os.path.dirname(OUT_TXT), exist_ok=True)
    with open(OUT_TXT, "w") as f:
        f.write(text + "\n")
    print(f"\nWrote {os.path.relpath(OUT_TXT, HERE)}")


if __name__ == "__main__":
    main()
