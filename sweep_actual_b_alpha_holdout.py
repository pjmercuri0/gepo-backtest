"""Holdout test of the principled actual-b ranker on 2025-2026.

Same scoring as sweep_actual_b_alpha.py (per-spread b from credit/max_loss,
per-spread α(b) = (b-1)/(2b)), but evaluated on the strict 2025-2026
holdout window. Reports canonical baseline alongside for direct comparison.
"""
from __future__ import annotations
import math
import os
import sys
from contextlib import contextmanager

import numpy as np
import pandas as pd

import config
import data_loader
import backtest
import spreads
import ground

# Import the principled scorer from the in-sample experiment
from sweep_actual_b_alpha import _score_row_principled


START   = "2025-01-01"
END     = "2026-05-09"
SIZING  = "1"
TOP_N   = 5


@contextmanager
def _patched_scorer():
    original = ground._score_row
    ground._score_row = _score_row_principled
    try:
        yield
    finally:
        ground._score_row = original


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


def _stats(label, weekly_df, trades_df):
    if weekly_df.empty:
        print(f"  {label}: no trades")
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
    print(f"  {label:38s}  trades={len(trades_df):4d}  final=${final:>10,.0f}  "
          f"Sharpe={sharpe:.2f}  yield={yld:5.2f}%  DD={dd:5.1f}%")


def main() -> int:
    print(f"\n== Holdout test of principled actual-b, qty=1, {START} → {END} ==\n")
    _setup()
    print("Loading data...")
    df_full       = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]
    print(f"  {len(df):,} option rows over the holdout\n")

    print(">>> canonical (b=1 implicit, α=-0.5)")
    ground.RANKING_MODE = "GROUND"
    ground.DKL_K = 20.0
    trades_c, weekly_c = backtest.run_backtest(
        df, expiry_prices, pd.DataFrame(), 0,
        top_n=TOP_N, sizing=SIZING,
        use_drift=False, drift_lookup=None,
    )
    _stats("canonical holdout", weekly_c, trades_c)

    print("\n>>> principled actual-b: per-spread b AND α(b) = (b-1)/(2b)")
    with _patched_scorer():
        trades_p, weekly_p = backtest.run_backtest(
            df, expiry_prices, pd.DataFrame(), 0,
            top_n=TOP_N, sizing=SIZING,
            use_drift=False, drift_lookup=None,
        )
    _stats("actual-b holdout", weekly_p, trades_p)

    return 0


if __name__ == "__main__":
    sys.exit(main())
