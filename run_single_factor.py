"""Single-factor baselines under the new canonical (actual-b, k=10).

Runs in-sample 2020-2024 qty=1 three times:
  - GROUND (joint, canonical k=10)
  - G_only (pure Kelly growth)
  - DKL_only (pure DKL, inverse — lower is preferred)

Prints results for the paper's single-factor baseline table.
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


def _stats(label, weekly_df, trades_df):
    if weekly_df.empty or trades_df.empty or "contracts" not in trades_df.columns:
        print(f"  {label}: no trades (weekly={len(weekly_df)}, trades={len(trades_df)})")
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
    print(f"\n== single-factor baselines (canonical k=10, actual-b), {START} → {END} ==\n")
    _setup()
    print("Loading data...")
    df_full       = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]
    print(f"  {len(df):,} option rows\n")

    ground.DKL_K = 10.0

    for mode in ["G_only", "DKL_only"]:
        ground.RANKING_MODE = mode
        # Single-factor modes need GROUND_THRESHOLD off (their score is sign-inverted
        # relative to GROUND; the canonical threshold would gate every trade).
        config.GROUND_THRESHOLD = float("-inf")
        trades, weekly = backtest.run_backtest(
            df, expiry_prices, pd.DataFrame(), 0,
            top_n=TOP_N, sizing=SIZING,
            use_drift=False, drift_lookup=None,
        )
        _stats(mode, weekly, trades)

    return 0


if __name__ == "__main__":
    sys.exit(main())
