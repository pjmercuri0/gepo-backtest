"""
Recompute historical pnl_per_contract using the corrected
spreads.calc_pnl (true piecewise-linear payoff vs the old midpoint-
symmetric approximation). Compares aggregate metrics old vs new on
the 4.5-year daily qty1 dataset.
"""
import numpy as np
import pandas as pd
from pathlib import Path
import spreads

ROOT = Path(__file__).resolve().parent
FILES = [
    "output/all_trades-daily-qty1-2022.csv",
    "output/all_trades-daily-qty1-2023.csv",
    "output/all_trades-daily-qty1-2024.csv",
    "output/all_trades-daily-qty1-oot.csv",
]


def old_pnl(outcome, credit, max_loss):
    """The buggy formula that just got replaced."""
    if outcome == 1.0:
        return credit
    if outcome == -1.0:
        return -max_loss
    if outcome > 0:
        return round(credit * outcome, 4)
    return round(max_loss * outcome, 4)


def new_pnl(spot, sp, bp, credit, max_loss, spread_type):
    return spreads.calc_pnl(spot, sp, bp, credit, max_loss, spread_type)


def summarize(label, df, col):
    if df.empty:
        return f"{label:30}  (empty)"
    pnl = df[col].astype(float)
    daily = df.groupby("entry_date")[col].sum()
    mu = daily.mean(); sig = daily.std(ddof=0)
    sharpe = (mu * np.sqrt(252) / sig) if sig > 0 else float("nan")
    eq = daily.cumsum()
    dd = (eq - eq.cummax()).min()
    return (f"{label:30}  n={len(df):>5}  "
            f"$tot={pnl.sum():>10,.0f}  $mu={pnl.mean():>6.2f}  "
            f"sharpe={sharpe:>5.2f}  dd=${dd:>9,.0f}")


def main():
    df = pd.concat([pd.read_csv(ROOT / f) for f in FILES], ignore_index=True)
    df["entry_date"]  = pd.to_datetime(df["entry_date"])
    df["expiry_date"] = pd.to_datetime(df["expiry_date"])
    print(f"Loaded {len(df):,} trades  ({df.entry_date.min().date()} → "
          f"{df.entry_date.max().date()})\n")

    # Old = the value stored in CSV (computed via the buggy formula)
    df["pnl_old"] = df["pnl_per_contract"].astype(float)

    # New = corrected piecewise-linear payoff
    df["pnl_new"] = df.apply(
        lambda r: new_pnl(r["expiry_price"], r["short_strike"], r["long_strike"],
                          r["net_credit"], r["max_loss"], r["decision"]),
        axis=1,
    )

    # Partial-only diff
    partials = df[(df["outcome"] != 1.0) & (df["outcome"] != -1.0)].copy()
    partials["delta"] = partials["pnl_new"] - partials["pnl_old"]
    print(f"Partial-outcome trades: {len(partials):,} of {len(df):,} "
          f"({100*len(partials)/len(df):.1f}%)")
    print(f"Avg per-trade delta on partials: ${partials['delta'].mean():+.4f}/share")
    print(f"Sum delta on partials:           ${partials['delta'].sum():+,.2f}\n")

    print("── full sample ──")
    print(summarize("OLD (buggy midpoint)", df, "pnl_old"))
    print(summarize("NEW (true payoff)",    df, "pnl_new"))
    delta = df["pnl_new"].sum() - df["pnl_old"].sum()
    print(f"  → net delta: ${delta:+,.2f} ({100*delta/abs(df['pnl_old'].sum() or 1):+.1f}% of old total)\n")

    print("── per-year ──")
    df["year"] = df["entry_date"].dt.year
    for y in sorted(df["year"].unique()):
        sub = df[df["year"] == y]
        print(summarize(f"  {y} OLD", sub, "pnl_old"))
        print(summarize(f"  {y} NEW", sub, "pnl_new"))


if __name__ == "__main__":
    main()
