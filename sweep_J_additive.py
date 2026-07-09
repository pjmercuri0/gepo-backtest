"""Test J_k = G - k·KL as a candidate ranker (additive form, not GROUND).

Tests whether ranking by the Hansen-Sargent / multiplier-preferences
functional J_k = G - k·KL works as well as GROUND = G·exp(-k·KL).
Runs at k ∈ {0.1, 1, 5, 10, 20} on in-sample 2020-2024.
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


START   = "2020-01-01"
END     = "2024-12-30"
SIZING  = "1"
TOP_N   = 5
K_GRID  = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0]
STARTING_BANKROLL = 10000.0


def _patched_compute_ground_J(week_df: pd.DataFrame) -> pd.DataFrame:
    """Score by J_k = G - k·KL instead of GROUND."""
    out = week_df.copy()
    out["GROUND"]    = np.nan
    out["is_ref_Rb"] = False
    out["G_ref"]     = np.nan
    out["DKL_ref"]   = np.nan
    out["DKL_diff"]  = np.nan
    out["G_diff"]    = np.nan

    valid = out[(out["G"].notna()) & (out["DKL"].notna()) & (out["DKL"] > 0)]
    if len(valid) < 2:
        return out

    ref_idx = valid["DKL"].idxmin()
    G_b   = valid.loc[ref_idx, "G"]
    DKL_b = valid.loc[ref_idx, "DKL"]
    out.loc[ref_idx, "is_ref_Rb"] = True

    for idx in valid.index:
        G_a   = out.loc[idx, "G"]
        DKL_a = out.loc[idx, "DKL"]
        # J_k = G - k·KL (additive)
        score = G_a - ground.DKL_K * DKL_a
        out.loc[idx, "GROUND"]   = round(score, 6)
        out.loc[idx, "G_ref"]    = G_b
        out.loc[idx, "DKL_ref"]  = DKL_b
        out.loc[idx, "G_diff"]   = round(G_a - G_b, 6)
        out.loc[idx, "DKL_diff"] = round(DKL_a - DKL_b, 6)
    return out


@contextmanager
def _patched_jk_ranker():
    original = ground._compute_ground_for_week
    ground._compute_ground_for_week = _patched_compute_ground_J
    # Disable threshold (J_k is negative so it would fail >= 0)
    orig_threshold = config.GROUND_THRESHOLD
    config.GROUND_THRESHOLD = float("-inf")
    try:
        yield
    finally:
        ground._compute_ground_for_week = original
        config.GROUND_THRESHOLD = orig_threshold


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


def _stats(label, trades, weekly):
    if weekly.empty or trades.empty or "contracts" not in trades.columns:
        print(f"  {label}: no trades")
        return
    final = float(weekly["bankroll_eow"].iloc[-1])
    wret = weekly["week_pnl"] / STARTING_BANKROLL
    sr = (wret.mean()/wret.std()*math.sqrt(52)) if wret.std() > 0 else 0
    rmx = weekly["bankroll_eow"].cummax()
    dd = ((weekly["bankroll_eow"]-rmx)/rmx).min() * 100
    pnl = (trades["contracts"]*trades["pnl_per_contract"]*100).sum()
    wagered = (trades["contracts"]*trades["max_loss"]*100).sum()
    yld = pnl/wagered*100 if wagered > 0 else 0
    print(f"  {label:18s}  trades={len(trades):4d}  final=${final:>9,.0f}  Sharpe={sr:.2f}  yield={yld:5.2f}%  DD={dd:5.1f}%")


def main() -> int:
    print(f"\n== J_k = G - k·KL (additive) sweep, qty=1 in-sample {START}→{END} ==\n")
    _setup()
    df_full = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]
    print(f"  {len(df):,} option rows\n")

    ground.RANKING_MODE = "GROUND"

    print(">>> baseline: canonical GROUND k=20 (Γ_k = G·exp(-k·KL)) [reference]")
    ground.DKL_K = 20.0
    trades_c, weekly_c = backtest.run_backtest(
        df, expiry_prices, pd.DataFrame(), 0,
        top_n=TOP_N, sizing=SIZING,
        use_drift=False, drift_lookup=None,
    )
    _stats("GROUND k=20", trades_c, weekly_c)

    print("\n>>> J_k = G - k·KL (additive ranker)")
    for k in K_GRID:
        ground.DKL_K = float(k)
        with _patched_jk_ranker():
            trades_j, weekly_j = backtest.run_backtest(
                df, expiry_prices, pd.DataFrame(), 0,
                top_n=TOP_N, sizing=SIZING,
                use_drift=False, drift_lookup=None,
            )
        _stats(f"J_k k={k}", trades_j, weekly_j)

    return 0


if __name__ == "__main__":
    sys.exit(main())
