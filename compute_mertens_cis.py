# -*- coding: utf-8 -*-
"""
Compute Mertens 95% confidence intervals for k ∈ {5, 10, 20, 50}
from the threshold-based sweep under the uncapped configuration.
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
TOP_N = 5
SIZING = "1"
SLIPPAGE = 0.0
K_VALUES_MERTENS = [5, 10, 20, 50]


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


def compute_mertens_ci(weekly_ret):
    """Compute Mertens 95% CI on Sharpe ratio."""
    n = len(weekly_ret)
    sharpe = (weekly_ret.mean() / weekly_ret.std() * math.sqrt(52)) if weekly_ret.std() > 0 else 0

    # Skewness and kurtosis
    gamma3 = stats.skew(weekly_ret)
    gamma4 = stats.kurtosis(weekly_ret, fisher=True)  # excess kurtosis

    # Mertens standard error
    se_sq = (1.0 / n) * (1 + 0.5 * sharpe**2 - gamma3 * sharpe + (gamma4 / 4.0) * sharpe**2)
    se = math.sqrt(se_sq)

    ci_lower = sharpe - 1.96 * se
    ci_upper = sharpe + 1.96 * se

    return {
        "sharpe": sharpe,
        "gamma3": gamma3,
        "gamma4": gamma4,
        "se": se,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
    }


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

    results = []
    for k in K_VALUES_MERTENS:
        ground.DKL_K = float(k)
        print(f"Computing Mertens CI for k={k}...", flush=True)

        trades_df, weekly_df = backtest.run_backtest(
            df_backtest, expiry_prices, pd.DataFrame(), 0,
            top_n=TOP_N, sizing=SIZING,
            use_drift=False, drift_lookup=None,
        )

        if not weekly_df.empty:
            weekly_ret = weekly_df["week_pnl"] / config.STARTING_BANKROLL
            ci_result = compute_mertens_ci(weekly_ret)
            ci_result["k"] = k
            results.append(ci_result)

            print(f"  k={k}: Sharpe={ci_result['sharpe']:.2f}, "
                  f"γ3={ci_result['gamma3']:+.2f}, γ4={ci_result['gamma4']:+.2f}, "
                  f"CI=[{ci_result['ci_lower']:.2f}, {ci_result['ci_upper']:.2f}]")

    print("\nTable 6 (Mertens CIs) — updated values for threshold-based selection:")
    print(f"{'k':>4s}  {'Sharpe':>7s}  {'γ₃':>7s}  {'γ₄':>7s}  {'σ_hat(Mertens)':>15s}  {'95% CI':>20s}")
    print("-" * 75)
    for r in results:
        print(f"{r['k']:>4.0f}  {r['sharpe']:>7.2f}  {r['gamma3']:>+7.2f}  {r['gamma4']:>+7.2f}  "
              f"{r['se']:>15.2f}  [{r['ci_lower']:.2f}, {r['ci_upper']:.2f}]")


if __name__ == "__main__":
    main()
