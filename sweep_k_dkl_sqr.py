"""Experimental k-sweep: Γ = G / 3^(DKL^(2/k)).

The "2" reflects user intuition that risk should scale quadratically.
At k=1 this gives DKL² (sharpest penalty at high DKL, vanishing penalty at low DKL).
At k=2 this gives DKL (linear identity).
At k>2 the exponent saturates toward 1.

Sweeps k over the same grid as sweep_k_invpow.py for direct comparison.
NO files written. Canonical results unchanged.
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


SWEEP_KS = [1, 2, 3, 5, 7, 10, 15, 20]
START    = "2020-01-01"
END      = "2024-12-30"
SIZING   = "2"
TOP_N    = 5


def _make_scorer(k: float):
    """Return a drop-in replacement for ground._compute_ground_for_week
    that uses Γ = G / 3^(DKL^(2/k))."""
    power = 2.0 / k

    def _score(week_df: pd.DataFrame) -> pd.DataFrame:
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
            if ground.RANKING_MODE == "G_only":
                score = G_a
            elif ground.RANKING_MODE == "DKL_only":
                score = -DKL_a
            else:
                exponent = DKL_a ** power if DKL_a > 0 else 0.0
                denom = 3.0 ** exponent
                score = G_a / denom
            out.loc[idx, "GROUND"]  = round(score, 8)
            out.loc[idx, "G_ref"]   = G_b
            out.loc[idx, "DKL_ref"] = DKL_b
            out.loc[idx, "G_diff"]  = G_a - G_b
            out.loc[idx, "DKL_diff"] = DKL_a - DKL_b
        return out

    return _score


@contextmanager
def _patched_scorer(scorer):
    original = ground._compute_ground_for_week
    ground._compute_ground_for_week = scorer
    try:
        yield
    finally:
        ground._compute_ground_for_week = original


def _setup_config():
    config.START_DATE = START
    config.END_DATE   = END
    config.SIZING     = SIZING


def _setup_filters():
    spy_csv = os.path.join(config.DATA_DIR, "spy_us_d.csv")
    spreads.REGIME_LOOKUP          = spreads.build_regime_lookup(spy_csv, sma_window=100)
    spreads.REGIME_FILTER          = True
    spreads.REGIME_PER_TICKER      = False
    spreads.GAP_FILTER             = False
    spreads.GAP_LOOKUP             = {}
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.VIX_LOOKUP             = {}
    spreads.SLIPPAGE_CENTS         = 0.0


def _stats(weekly_df: pd.DataFrame, trades_df: pd.DataFrame) -> dict:
    if weekly_df.empty:
        return None
    final = float(weekly_df["bankroll_eow"].iloc[-1])
    n_weeks = len(weekly_df)
    total_roi = (final / config.STARTING_BANKROLL - 1) * 100
    ann = total_roi / (n_weeks / 52) if n_weeks > 0 else 0
    weekly_ret = weekly_df["week_pnl"] / config.STARTING_BANKROLL
    sharpe = (weekly_ret.mean() / weekly_ret.std() * math.sqrt(52)) \
        if weekly_ret.std() > 0 else 0
    rmx = weekly_df["bankroll_eow"].cummax()
    dd = ((weekly_df["bankroll_eow"] - rmx) / rmx).min() * 100
    n_trades = len(trades_df)
    pnl     = trades_df["contracts"] * trades_df["pnl_per_contract"] * 100
    wagered = trades_df["contracts"] * trades_df["max_loss"] * 100
    yield_pct = pnl.sum() / wagered.sum() * 100 if wagered.sum() > 0 else 0
    return dict(n_trades=n_trades, final=final, ann=ann, sharpe=sharpe,
                dd=dd, yield_pct=yield_pct)


def _run_one(label: str, df, expiry_prices) -> dict | None:
    print(f"  → {label} ...", flush=True)
    trades_df, weekly_df = backtest.run_backtest(
        df, expiry_prices, pd.DataFrame(), 0,
        top_n=TOP_N, sizing=SIZING,
        use_drift=False, drift_lookup=None,
    )
    if trades_df.empty:
        print(f"     ! no trades")
        return None
    return _stats(weekly_df, trades_df)


def main() -> int:
    print(f"\n== DKL-squared k-sweep on qty=2 in-sample ==")
    print(f"   Γ = G / 3^(DKL^(2/k)),  window {START} → {END}\n")

    _setup_config()
    print("Loading data ...", flush=True)
    df_full       = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    _setup_filters()
    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]
    print(f"  {len(df):,} option rows\n", flush=True)

    rows = []

    ground.RANKING_MODE = "GROUND"
    ground.DKL_K = 20.0
    s = _run_one("canonical  Γ = G / 3^(20·DKL)", df, expiry_prices)
    if s:
        s["label"] = "canonical k=20"; s["k"] = 20; rows.append(s)

    ground.RANKING_MODE = "G_only"
    s = _run_one("G-only", df, expiry_prices)
    if s:
        s["label"] = "G-only baseline"; s["k"] = None; rows.append(s)

    ground.RANKING_MODE = "GROUND"
    for k in SWEEP_KS:
        scorer = _make_scorer(float(k))
        with _patched_scorer(scorer):
            s = _run_one(f"dkl-sqr  k={k}  (denom = 3^(DKL^({2.0/k:.3f})))", df, expiry_prices)
        if s:
            s["label"] = f"dkl-sqr k={k}"; s["k"] = k; rows.append(s)

    if not rows:
        print("no runs produced trades")
        return 1

    print("\n==== results ====")
    print(f"  {'config':<28}  {'trades':>6}  {'sharpe':>7}  "
          f"{'yield':>7}  {'ann':>7}  {'DD':>7}  {'final':>10}")
    for r in rows:
        print(f"  {r['label']:<28}  {r['n_trades']:>6,}  "
              f"{r['sharpe']:>7.2f}  {r['yield_pct']:>6.2f}%  "
              f"{r['ann']:>6.1f}%  {r['dd']:>6.1f}%  "
              f"${r['final']:>9,.0f}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
