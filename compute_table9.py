# -*- coding: utf-8 -*-
"""
Compute Table 9 single-factor baseline comparison.
All three rankers under top-N=5 selection, k=20.
"""
import math
import os
import pandas as pd

import config
import data_loader
import backtest
import spreads
import ground


START = "2020-01-01"
END = "2024-12-30"
TOP_N = 5
SIZING = "1"
SLIPPAGE = 0.0


def setup_config():
    config.START_DATE = START
    config.END_DATE = END


def setup_filters():
    spy_csv = os.path.join(config.DATA_DIR, "spy_us_d.csv")
    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(spy_csv, sma_window=100)
    spreads.REGIME_FILTER = True
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.GAP_LOOKUP = {}
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.VIX_LOOKUP = {}
    spreads.SLIPPAGE_CENTS = SLIPPAGE
    config.MAX_CREDIT_RATIO = config.BACKTEST_MAX_CREDIT_RATIO


def stats_for(weekly_df, trades_df):
    if weekly_df.empty:
        return None
    final = float(weekly_df["bankroll_eow"].iloc[-1])
    total_roi = (final / config.STARTING_BANKROLL - 1) * 100
    n_weeks = len(weekly_df)
    ann = total_roi / (n_weeks / 52) if n_weeks > 0 else 0
    weekly_ret = weekly_df["week_pnl"] / config.STARTING_BANKROLL
    sharpe = (weekly_ret.mean() / weekly_ret.std() * math.sqrt(52)) \
        if weekly_ret.std() > 0 else 0
    rmx = weekly_df["bankroll_eow"].cummax()
    dd = ((weekly_df["bankroll_eow"] - rmx) / rmx).min() * 100
    n_trades = len(trades_df)
    n_wins = int((trades_df["result"] == "WIN").sum())
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
    backtest_end = pd.Timestamp(END)
    df_backtest = df_full[
        (df_full["DataDate"] >= backtest_start) &
        (df_full["DataDate"] <= backtest_end)
    ]

    ground.DKL_K = 20.0

    # Run with GROUND ranker
    print("Computing GROUND (k=20) with top-N=5...")
    trades_df_ground, weekly_df_ground = backtest.run_backtest(
        df_backtest, expiry_prices, pd.DataFrame(), 0,
        top_n=TOP_N, sizing=SIZING,
        use_drift=False, drift_lookup=None,
    )
    s_ground = stats_for(weekly_df_ground, trades_df_ground)

    print(f"GROUND (k=20) top-N=5:")
    print(f"  Trades: {s_ground['n_trades']}")
    print(f"  Final: ${s_ground['final']:,.0f}")
    print(f"  Sharpe: {s_ground['sharpe']:.2f}")
    print(f"  Max DD: {s_ground['dd']:.1f}%")
    print(f"  Yield: {(s_ground['final']/config.STARTING_BANKROLL - 1)*100:.2f}%")

    # Compute yield manually
    total_pnl = s_ground['final'] - config.STARTING_BANKROLL
    wagered = 0
    for _, trade in trades_df_ground.iterrows():
        wagered += abs(trade['max_loss'])
    yield_pct = (total_pnl / wagered * 100) if wagered > 0 else 0

    print(f"\nTable 9 GROUND row:")
    print(f"GROUND (k=20): {s_ground['n_trades']:,} trades, ${s_ground['final']:,.0f}, Sharpe {s_ground['sharpe']:.2f}, DD {s_ground['dd']:.1f}%, Yield {yield_pct:.2f}%")


if __name__ == "__main__":
    main()
