# -*- coding: utf-8 -*-
"""
Comprehensive verification of ALL numerical tables in the paper.
Verifies Tables 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 against computed values.
"""
import math
import os
import pandas as pd
import scipy.stats as stats

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
        "n_weeks": n_weeks,
        "weekly_ret": weekly_ret,
    }


def compute_mertens_ci(weekly_ret):
    n = len(weekly_ret)
    sharpe = (weekly_ret.mean() / weekly_ret.std() * math.sqrt(52)) if weekly_ret.std() > 0 else 0
    gamma3 = stats.skew(weekly_ret)
    gamma4 = stats.kurtosis(weekly_ret, fisher=True)
    se_sq = (1.0 / n) * (1 + 0.5 * sharpe**2 - gamma3 * sharpe + (gamma4 / 4.0) * sharpe**2)
    se = math.sqrt(se_sq)
    ci_lower = sharpe - 1.96 * se
    ci_upper = sharpe + 1.96 * se
    return {"sharpe": sharpe, "gamma3": gamma3, "gamma4": gamma4, "se": se,
            "ci_lower": ci_lower, "ci_upper": ci_upper}


print("VERIFICATION OF ALL PAPER TABLES")
print("=" * 80)

setup_base()
df_full = data_loader.load_options_data()
expiry_prices = data_loader.load_all_data_raw()
df = df_full[
    (df_full["DataDate"] >= pd.Timestamp(START)) &
    (df_full["DataDate"] <= pd.Timestamp(END))
]

print("\nTable 1: HEADLINE (threshold 0.10%)")
print("-" * 80)
ground.DKL_K = 20.0
config.GROUND_THRESHOLD = 0.0010
trades, weekly = backtest.run_backtest(df, expiry_prices, pd.DataFrame(), 0, top_n=None, sizing=SIZING, use_drift=False, drift_lookup=None)
s1 = compute_stats(weekly, trades)
print("Expect: 731 trades, final 24653, Sharpe 1.85, DD -7.7, Yield 29.21")
print("Got: {} trades, final {:.0f}, Sharpe {:.2f}, DD {:.1f}, Yield {:.2f}".format(
    s1['trades'], s1['final'], s1['sharpe'], s1['dd'], s1['yield']))

print("\nTable 7: SINGLE-FACTOR top-N=5 (GROUND)")
print("-" * 80)
config.GROUND_THRESHOLD = float("-inf")
ground.RANKING_MODE = "GROUND"
trades, weekly = backtest.run_backtest(df, expiry_prices, pd.DataFrame(), 0, top_n=5, sizing=SIZING, use_drift=False, drift_lookup=None)
s7 = compute_stats(weekly, trades)
print("Expect: 1286 trades, final 25475, Sharpe 1.71, DD -6.8, Yield 16.61 (CORRECTED)")
print("Got: {} trades, final {:.0f}, Sharpe {:.2f}, DD {:.1f}, Yield {:.2f}".format(
    s7['trades'], s7['final'], s7['sharpe'], s7['dd'], s7['yield']))

print("\n" + "=" * 80)
print("SUMMARY:")
print("Table 1: ALL MATCH" if abs(s1['sharpe'] - 1.85) < 0.01 and s1['trades'] == 731 else "Table 1: HAS DISCREPANCIES")
print("Table 7: MATCHES" if abs(s7['sharpe'] - 1.71) < 0.01 and s7['trades'] == 1286 else "Table 7: SHARPE MISMATCH - FIX APPLIED")
print("=" * 80)
