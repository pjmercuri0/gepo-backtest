"""
Clean qty1 Mon-Thu daily backtest. Top-5 GROUND picks per day (as already
selected by build_candidates + ground.score in the existing all_trades CSVs),
P&L re-computed under the CORRECTED piecewise-linear payoff formula at a
chosen fill basis. Optional SPY-rv20 vol-gate (skip non-Monday days when
SPY 20-day realized vol >= 20%).

Run:  python3 clean_backtest.py
"""
import argparse, numpy as np, pandas as pd
from pathlib import Path
import spreads

ROOT = Path(__file__).resolve().parent
TRADE_FILES = [
    "output/all_trades-daily-qty1-2022.csv",
    "output/all_trades-daily-qty1-2023.csv",
    "output/all_trades-daily-qty1-2024.csv",
    "output/all_trades-daily-qty1-oot.csv",
]
SPY_CSV   = "data/spy_us_d.csv"
RV_THRESH = 20.0
START     = 10000


def load_trades() -> pd.DataFrame:
    df = pd.concat([pd.read_csv(ROOT / f) for f in TRADE_FILES], ignore_index=True)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["year"]       = df["entry_date"].dt.year
    df["dow"]        = df["entry_date"].dt.dayofweek    # Mon=0
    return df


def attach_rv20(df: pd.DataFrame) -> pd.DataFrame:
    spy = pd.read_csv(ROOT / SPY_CSV, parse_dates=["Date"]).sort_values("Date").reset_index(drop=True)
    spy["ret"]  = spy["Close"].pct_change()
    spy["rv20"] = spy["ret"].rolling(20).std() * np.sqrt(252) * 100
    spy_idx = pd.DatetimeIndex(spy["Date"])
    def rv_for(d):
        pos = spy_idx.searchsorted(d, side="right") - 1
        return float(spy.iloc[pos]["rv20"]) if pos >= 0 else None
    df["rv20"]      = df["entry_date"].apply(rv_for)
    df["vol_gated"] = (df["dow"] != 0) & (df["rv20"] >= RV_THRESH)
    return df


def recompute_pnl(df: pd.DataFrame, pct: float) -> pd.DataFrame:
    out = df.copy()
    out["width"]     = out["net_credit"] + out["max_loss"]
    out["credit_p"]  = out["net_credit"] * pct
    out["ml_p"]      = out["width"] - out["credit_p"]
    out["pnl_share"] = out.apply(
        lambda r: spreads.calc_pnl(
            r["expiry_price"], r["short_strike"], r["long_strike"],
            r["credit_p"], r["ml_p"], r["decision"]
        ), axis=1
    )
    out["dollar_pnl"] = out["pnl_share"] * 100
    return out


def yearly_metrics(df: pd.DataFrame, label: str) -> None:
    print(f"\n=== {label} ===")
    print(f"{'Year':<8} {'Trades':>7} {'Days':>5} {'Profit':>11} {'Wallet':>11} {'Sharpe':>8} {'Win%':>6} {'Max DD':>11}")
    print("-" * 78)
    wallet = START
    cum_daily = pd.Series(dtype=float)
    for y in sorted(df["year"].unique()):
        sub  = df[df["year"] == y]
        ntr  = len(sub)
        ndays = sub["entry_date"].nunique()
        pnl   = sub["dollar_pnl"].sum()
        wallet_end = wallet + pnl
        win   = 100 * (sub["dollar_pnl"] > 0).mean()
        daily = sub.groupby("entry_date")["dollar_pnl"].sum().sort_index()
        sg    = daily.std(ddof=0)
        sh    = (daily.mean() * np.sqrt(252) / sg) if sg > 0 else 0
        eq    = wallet + daily.cumsum()
        dd    = (eq - eq.cummax()).min()
        print(f"{y:<8} {ntr:>7} {ndays:>5} ${pnl:>+10,.0f} ${wallet_end:>10,.0f} {sh:>+8.2f} {win:>5.1f}% ${dd:>+10,.0f}")
        wallet = wallet_end
    # Total
    ntr   = len(df); ndays = df["entry_date"].nunique()
    pnl   = df["dollar_pnl"].sum()
    win   = 100 * (df["dollar_pnl"] > 0).mean()
    daily = df.groupby("entry_date")["dollar_pnl"].sum().sort_index()
    sg    = daily.std(ddof=0)
    sh    = (daily.mean() * np.sqrt(252) / sg) if sg > 0 else 0
    eq    = START + daily.cumsum()
    dd    = (eq - eq.cummax()).min()
    print("-" * 78)
    print(f"{'4.5yr':<8} {ntr:>7} {ndays:>5} ${pnl:>+10,.0f} ${START+pnl:>10,.0f} {sh:>+8.2f} {win:>5.1f}% ${dd:>+10,.0f}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pct", type=float, nargs="+", default=[0.75, 0.80, 0.82, 0.85, 1.00],
                   help="Fill basis (fraction of mid) to evaluate, space-separated")
    p.add_argument("--no-vol-gate", action="store_true",
                   help="Disable vol-gate (show raw, no-filter results)")
    args = p.parse_args()

    df = load_trades()
    df = attach_rv20(df)
    print(f"Loaded {len(df):,} trades over {df['entry_date'].nunique():,} trading days "
          f"({df['entry_date'].min().date()} → {df['entry_date'].max().date()})")
    print(f"Vol-gate would drop {df['vol_gated'].sum():,} non-Monday trades "
          f"({100 * df['vol_gated'].mean():.1f}%)")

    df_keep = df if args.no_vol_gate else df[~df["vol_gated"]]
    gate_label = "no vol-gate" if args.no_vol_gate else f"vol-gate rv20≥{RV_THRESH:.0f}%"
    print(f"\nAfter {gate_label}: {len(df_keep):,} trades")

    for pct in args.pct:
        recomputed = recompute_pnl(df_keep, pct)
        yearly_metrics(recomputed, f"{pct*100:.0f}% × mid · {gate_label}")

    return 0


if __name__ == "__main__":
    main()
