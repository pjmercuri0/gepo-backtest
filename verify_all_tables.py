# -*- coding: utf-8 -*-
"""
Comprehensive verification of all paper table values.
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
SIZING = "1"


def setup_base():
    config.START_DATE = START
    config.END_DATE = END
    spy_csv = os.path.join(config.DATA_DIR, "spy_us_d.csv")
    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(spy_csv, sma_window=100)
    spreads.REGIME_FILTER = True
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.GAP_LOOKUP = {}
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.VIX_LOOKUP = {}
    spreads.SLIPPAGE_CENTS = 0.0
    config.MAX_CREDIT_RATIO = config.BACKTEST_MAX_CREDIT_RATIO


def compute_stats(weekly_df, trades_df):
    """Compute Sharpe, DD, final, yield metrics."""
    if weekly_df.empty or trades_df.empty:
        return None

    final = float(weekly_df["bankroll_eow"].iloc[-1])
    n_weeks = len(weekly_df)
    weekly_ret = weekly_df["week_pnl"] / config.STARTING_BANKROLL
    sharpe = (weekly_ret.mean() / weekly_ret.std() * math.sqrt(52)) if weekly_ret.std() > 0 else 0
    rmx = weekly_df["bankroll_eow"].cummax()
    dd = ((weekly_df["bankroll_eow"] - rmx) / rmx).min() * 100

    pnl = (trades_df["contracts"] * trades_df["pnl_per_contract"] * 100).sum()
    wagered = (trades_df["contracts"] * trades_df["max_loss"] * 100).sum()
    yld = pnl / wagered * 100 if wagered > 0 else 0

    return {
        "trades": len(trades_df),
        "final": final,
        "sharpe": sharpe,
        "dd": dd,
        "yield": yld,
    }


def test_table_1():
    print("\n=== TABLE 1: HEADLINE (threshold 0.10 percent) ===")
    setup_base()
    ground.DKL_K = 20.0
    config.GROUND_THRESHOLD = 0.0010

    df_full = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]

    trades_df, weekly_df = backtest.run_backtest(
        df, expiry_prices, pd.DataFrame(), 0,
        top_n=None,
        sizing=SIZING,
        use_drift=False, drift_lookup=None,
    )

    stats = compute_stats(weekly_df, trades_df)
    print("GROUND (k=20, threshold=0.10 percent):")
    print("  Paper expects: 731 trades, final 24653, Sharpe 1.85, DD -7.7, Yield 29.21")
    computed_line = "  Computed: {} trades, final {:.0f}, Sharpe {:.2f}, DD {:.1f}, Yield {:.2f}".format(
        stats['trades'], stats['final'], stats['sharpe'], stats['dd'], stats['yield']
    )
    print(computed_line)


def test_table_5_k20():
    print("\n=== TABLE 5: K-SWEEP (k=20, threshold 0.0) ===")
    setup_base()
    ground.DKL_K = 20.0
    config.GROUND_THRESHOLD = 0.0

    df_full = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]

    trades_df, weekly_df = backtest.run_backtest(
        df, expiry_prices, pd.DataFrame(), 0,
        top_n=None,
        sizing=SIZING,
        use_drift=False, drift_lookup=None,
    )

    stats = compute_stats(weekly_df, trades_df)
    print("k=20 (canonical):")
    print("  Paper expects: 659 trades, final 22263, Sharpe 1.87, DD -6.8, Yield 24.5")
    computed_line = "  Computed: {} trades, final {:.0f}, Sharpe {:.2f}, DD {:.1f}, Yield {:.2f}".format(
        stats['trades'], stats['final'], stats['sharpe'], stats['dd'], stats['yield']
    )
    print(computed_line)


def test_table_7():
    print("\n=== TABLE 7: SINGLE-FACTOR (top-N=5, no threshold) ===")
    setup_base()
    config.GROUND_THRESHOLD = float("-inf")
    ground.DKL_K = 20.0

    df_full = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]

    for mode in ["GROUND", "G_only", "DKL_only"]:
        ground.RANKING_MODE = mode
        trades_df, weekly_df = backtest.run_backtest(
            df, expiry_prices, pd.DataFrame(), 0,
            top_n=5,
            sizing=SIZING,
            use_drift=False, drift_lookup=None,
        )
        stats = compute_stats(weekly_df, trades_df)

        if mode == "GROUND":
            print("GROUND (k=20, top-N=5):")
            print("  Paper expects: 1286 trades, Sharpe 1.85, Yield 16.61")
            computed_line = "  Computed: {} trades, Sharpe {:.2f}, Yield {:.2f}".format(
                stats['trades'], stats['sharpe'], stats['yield']
            )
            print(computed_line)
        elif mode == "G_only":
            print("Kelly EV alone (top-N=5):")
            computed_line = "  Computed: {} trades, Sharpe {:.2f}, Yield {:.2f}".format(
                stats['trades'], stats['sharpe'], stats['yield']
            )
            print(computed_line)
        else:
            print("KL alone (top-N=5):")
            computed_line = "  Computed: {} trades, Sharpe {:.2f}, Yield {:.2f}".format(
                stats['trades'], stats['sharpe'], stats['yield']
            )
            print(computed_line)


if __name__ == "__main__":
    print("Verifying key paper table values...")
    test_table_1()
    test_table_5_k20()
    test_table_7()
    print("\n=== VERIFICATION COMPLETE ===")
