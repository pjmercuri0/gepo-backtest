# -*- coding: utf-8 -*-
"""Single-factor baselines: GROUND vs Kelly EV alone vs DKL alone.

In-sample 2020–2024, qty=1, top-N=5 selection rule.
All three rankers use the same candidate pool and selection mechanism
for a fair comparison.
"""
from __future__ import annotations
import math
import os
import sys

import pandas as pd

import config
import data_loader
import backtest
import spreads
import ground


START   = "2020-01-01"
END     = "2024-12-30"
SIZING  = "1"
TOP_N   = 5


def _setup():
    config.START_DATE = START
    config.END_DATE   = END
    config.SIZING     = SIZING
    spy_csv = os.path.join(config.DATA_DIR, "spy_us_d.csv")
    spreads.REGIME_LOOKUP          = spreads.build_regime_lookup(spy_csv, sma_window=100)
    spreads.REGIME_FILTER          = True
    spreads.REGIME_PER_TICKER      = False
    spreads.GAP_FILTER             = False
    spreads.GAP_LOOKUP             = {}
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.VIX_LOOKUP             = {}
    spreads.SLIPPAGE_CENTS         = 0.0
    # Use canonical k=20
    ground.DKL_K = 20.0


def _stats(label, weekly_df, trades_df):
    if weekly_df.empty or trades_df.empty or "contracts" not in trades_df.columns:
        print(f"  {label:40s}: no trades")
        return
    final     = float(weekly_df["bankroll_eow"].iloc[-1])
    weekly_ret = weekly_df["week_pnl"] / config.STARTING_BANKROLL
    sharpe = (weekly_ret.mean() / weekly_ret.std() * math.sqrt(52)) \
        if weekly_ret.std() > 0 else 0
    rmx = weekly_df["bankroll_eow"].cummax()
    dd  = ((weekly_df["bankroll_eow"] - rmx) / rmx).min() * 100
    pnl     = (trades_df["contracts"] * trades_df["pnl_per_contract"] * 100).sum()
    wagered = (trades_df["contracts"] * trades_df["max_loss"] * 100).sum()
    yld = pnl / wagered * 100 if wagered > 0 else 0
    print(f"  {label:40s}  trades={len(trades_df):4d}  final=${final:>10,.0f}  "
          f"Sharpe={sharpe:.2f}  yield={yld:5.2f}%  DD={dd:5.1f}%")


def main() -> int:
    print(f"\n== single-factor baselines (k=20, canonical config), {START} → {END} ==\n")
    _setup()
    print("Loading data...")
    df_full       = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]
    print(f"  {len(df):,} option rows\n")

    # Run all three rankers with top-N=5, threshold off
    for mode in ["GROUND", "G_only", "DKL_only"]:
        print(f"Running {mode}...")
        ground.RANKING_MODE = mode
        config.GROUND_THRESHOLD = float("-inf")  # Off; rely on top-N=5
        trades, weekly = backtest.run_backtest(
            df, expiry_prices, pd.DataFrame(), 0,
            top_n=TOP_N, sizing=SIZING,
            use_drift=False, drift_lookup=None,
        )
        _stats(mode, weekly, trades)

    return 0


if __name__ == "__main__":
    sys.exit(main())
