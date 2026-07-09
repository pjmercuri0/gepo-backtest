"""
Sweep DKL_K (the GROUND v3 amplification factor) on a holdout subset of
the data. Train period: 2020-2024 only (out-of-sample on 2025-2026 saved
for honest forward-validation later).

Output: console table comparing strategy performance across k values.
"""
import math
import os
import sys

import pandas as pd

import config
import data_loader
import backtest
import spreads
import ground


# Tune-on-2020-2024-only — out-of-sample 2025+ kept clean
START      = "2020-01-01"
END        = "2024-12-30"
TOP_N      = 5
SIZING     = "1"                  # qty 1
THETA_MIN  = float("-inf")        # canonical = theta filter OFF
SLIPPAGE   = 0.0                  # canonical = 0¢ slippage

# Sweep grid — full range to match Table 5 in paper
K_VALUES   = [0.5, 1, 2, 5, 10, 20, 50, 100, 200]


def setup_config():
    """Window + theta-filter overrides only. Rest from config.py canonical defaults."""
    config.START_DATE = START
    config.END_DATE   = END
    if THETA_MIN != float("-inf"):
        config.MIN_THETA_CREDIT_RATIO = THETA_MIN


def setup_filters():
    spy_csv = os.path.join(config.DATA_DIR, "spy_us_d.csv")
    spreads.REGIME_LOOKUP     = spreads.build_regime_lookup(spy_csv, sma_window=100)
    spreads.REGIME_FILTER     = True
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER             = False
    spreads.GAP_LOOKUP             = {}
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.VIX_LOOKUP             = {}
    spreads.SLIPPAGE_CENTS         = SLIPPAGE
    # Apply canonical MAX_CREDIT_RATIO from config (no cap).
    config.MAX_CREDIT_RATIO        = config.BACKTEST_MAX_CREDIT_RATIO


def stats_for(weekly_df, trades_df):
    if weekly_df.empty:
        return None
    final     = float(weekly_df["bankroll_eow"].iloc[-1])
    total_roi = (final / config.STARTING_BANKROLL - 1) * 100
    n_weeks   = len(weekly_df)
    ann       = total_roi / (n_weeks / 52) if n_weeks > 0 else 0
    weekly_ret = weekly_df["week_pnl"] / config.STARTING_BANKROLL
    sharpe = (weekly_ret.mean() / weekly_ret.std() * math.sqrt(52)) \
        if weekly_ret.std() > 0 else 0
    rmx = weekly_df["bankroll_eow"].cummax()
    dd  = ((weekly_df["bankroll_eow"] - rmx) / rmx).min() * 100
    n_trades = len(trades_df)
    n_wins   = int((trades_df["result"] == "WIN").sum())
    win_rate = n_wins / n_trades * 100 if n_trades > 0 else 0
    calmar = ann / abs(dd) if dd != 0 else 0
    return dict(
        n_trades=n_trades, win_rate=win_rate,
        final=final, ann=ann, sharpe=sharpe, dd=dd, calmar=calmar,
    )


def main():
    setup_config()
    df_full = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    setup_filters()

    backtest_start = pd.Timestamp(START)
    backtest_end   = pd.Timestamp(END)
    df_backtest = df_full[
        (df_full["DataDate"] >= backtest_start) &
        (df_full["DataDate"] <= backtest_end)
    ]
    print(f"Train window: {START} → {END}  ({len(df_backtest):,} rows)")
    print(f"Sizing: qty {SIZING}, theta filter ON, slippage {SLIPPAGE}¢/leg\n")

    progress_csv = os.path.join(config.OUTPUT_DIR, "_sweep_k_progress.csv")
    rows = []
    for k in K_VALUES:
        ground.DKL_K = float(k)
        print(f"  Running k={k}...", flush=True)
        trades_df, weekly_df = backtest.run_backtest(
            df_backtest, expiry_prices, pd.DataFrame(), 0,
            top_n=TOP_N, sizing=SIZING,
            use_drift=False, drift_lookup=None,
        )
        s = stats_for(weekly_df, trades_df)
        if s is not None:
            s["k"] = k
            rows.append(s)
            # Persist progress after every k so partial sweeps don't lose data
            pd.DataFrame(rows).to_csv(progress_csv, index=False)
            print(f"    k={k} done: Sharpe {s['sharpe']:.2f}, "
                  f"ann {s['ann']:.1f}%, DD {s['dd']:.1f}%", flush=True)

    print()
    print(f"{'k':>4s}  {'trades':>7s}  {'win':>6s}  {'final':>10s}  "
          f"{'ann':>7s}  {'sharpe':>7s}  {'dd':>7s}  {'calmar':>7s}")
    print("-" * 70)
    for r in rows:
        print(f"{r['k']:>4.0f}  {r['n_trades']:>7,}  {r['win_rate']:>5.1f}%  "
              f"${r['final']:>9,.0f}  {r['ann']:>6.1f}%  {r['sharpe']:>7.2f}  "
              f"{r['dd']:>6.1f}%  {r['calmar']:>7.2f}")

    # Best by Sharpe
    best = max(rows, key=lambda r: r["sharpe"])
    print(f"\nBest by Sharpe: k={best['k']:.0f} (Sharpe {best['sharpe']:.2f}, "
          f"ann {best['ann']:.1f}%, DD {best['dd']:.1f}%)")
    best_calmar = max(rows, key=lambda r: r["calmar"])
    print(f"Best by Calmar: k={best_calmar['k']:.0f} (Calmar {best_calmar['calmar']:.2f}, "
          f"Sharpe {best_calmar['sharpe']:.2f}, ann {best_calmar['ann']:.1f}%)")


if __name__ == "__main__":
    main()
