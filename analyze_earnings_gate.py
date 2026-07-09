"""
Test impact of an earnings-window gate on the existing 4.5-year daily qty1
backtest. Reject any trade whose (entry_date, expiry_date] holding window
contains an earnings date for that ticker.

Compares baseline (no gate) vs gated subsets on:
  - total $, mean/trade, win rate, count
  - Sharpe (sqrt(252) annualization, daily-cadence)
  - max drawdown
  - per-year breakdown
  - per-fill-basis sensitivity (mid, 80%, 75% if columns present)
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent

TRADE_FILES = [
    "output/all_trades-daily-qty1-2022.csv",
    "output/all_trades-daily-qty1-2023.csv",
    "output/all_trades-daily-qty1-2024.csv",
    "output/all_trades-daily-qty1-oot.csv",
]
EARNINGS_CSV = "data/earnings_calendar.csv"


def load_trades() -> pd.DataFrame:
    dfs = [pd.read_csv(ROOT / f) for f in TRADE_FILES]
    df = pd.concat(dfs, ignore_index=True)
    df["entry_date"]  = pd.to_datetime(df["entry_date"])
    df["expiry_date"] = pd.to_datetime(df["expiry_date"])
    return df


def load_earnings() -> dict:
    df = pd.read_csv(ROOT / EARNINGS_CSV)
    df["EarningsDate"] = pd.to_datetime(df["EarningsDate"])
    return {
        sym: pd.DatetimeIndex(sorted(grp["EarningsDate"].unique()))
        for sym, grp in df.groupby("Symbol")
    }


def flag_gated(trades: pd.DataFrame, earnings: dict) -> pd.Series:
    """True if any earnings date for the ticker is in (entry, expiry]."""
    flags = np.zeros(len(trades), dtype=bool)
    for i, row in enumerate(trades.itertuples(index=False)):
        arr = earnings.get(row.ticker)
        if arr is None or len(arr) == 0:
            continue
        lo = arr.searchsorted(row.entry_date,  side="right")
        hi = arr.searchsorted(row.expiry_date, side="right")
        if hi > lo:
            flags[i] = True
    return pd.Series(flags, index=trades.index)


def summarize(label: str, trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"label": label, "n": 0}
    pnl = trades["pnl_per_contract"].astype(float)
    wins = (pnl > 0).sum()
    daily = trades.groupby("entry_date")["pnl_per_contract"].sum()
    mu = daily.mean()
    sig = daily.std(ddof=0)
    sharpe = (mu * np.sqrt(252) / sig) if sig > 0 else np.nan
    eq = daily.cumsum()
    dd = (eq - eq.cummax()).min()
    return {
        "label":   label,
        "n":       len(trades),
        "total":   pnl.sum(),
        "mean":    pnl.mean(),
        "win_pct": 100 * wins / len(trades),
        "sharpe":  sharpe,
        "max_dd":  dd,
    }


def fmt(d: dict) -> str:
    if d.get("n", 0) == 0:
        return f"{d['label']:18}  (no trades)"
    return (f"{d['label']:18}  n={d['n']:>5}  "
            f"$tot={d['total']:>10,.0f}  $mu={d['mean']:>6.1f}  "
            f"win={d['win_pct']:>5.1f}%  sharpe={d['sharpe']:>5.2f}  "
            f"dd=${d['max_dd']:>9,.0f}")


def main() -> int:
    trades = load_trades()
    earnings = load_earnings()
    print(f"Loaded {len(trades):,} trades  ({trades.entry_date.min().date()} → "
          f"{trades.entry_date.max().date()})")
    print(f"Earnings CSV: {sum(len(a) for a in earnings.values()):,} dates across "
          f"{len(earnings):,} tickers")

    gated = flag_gated(trades, earnings)
    print(f"\nGate would reject {gated.sum():,} of {len(trades):,} trades "
          f"({100*gated.mean():.1f}%)\n")

    baseline = summarize("baseline (no gate)", trades)
    kept     = summarize("after gate",         trades[~gated])
    dropped  = summarize("DROPPED by gate",    trades[gated])
    print(fmt(baseline))
    print(fmt(kept))
    print(fmt(dropped))

    print(f"\nNet $ delta (kept - baseline): ${kept['total']-baseline['total']:+,.0f}")
    print(f"DD improvement: ${baseline['max_dd']-kept['max_dd']:+,.0f}")
    print(f"Sharpe delta:    {kept['sharpe']-baseline['sharpe']:+.2f}")

    print("\n── per-year ──")
    trades["year"] = trades["entry_date"].dt.year
    for y in sorted(trades["year"].unique()):
        sub  = trades[trades["year"] == y]
        sub_g = sub[~gated.loc[sub.index]]
        b = summarize(f"  {y} baseline", sub)
        g = summarize(f"  {y} gated",    sub_g)
        print(fmt(b))
        print(fmt(g))

    return 0


if __name__ == "__main__":
    sys.exit(main())
