"""Principled actual-b experiment: not only the win payoff b but also
the partial-multiplier α derived from the linear payoff geometry between
strikes.

Assuming the underlying expires uniformly in the partial zone [K_long,
K_short], the partial P&L per unit at risk ranges from +b (at K_short)
to -1 (at K_long), with mean (b-1)/2. Setting αb = (b-1)/2 gives the
per-spread principled α:

    α(b) = (b - 1) / (2b)

For b = 0.5 this reduces to α = -0.5 (the canonical constant). For b =
0.3, α = -1.167 (partial is closer to a full-loss); for b = 0.8,
α = -0.125 (partial is mild). Runs qty=1 in-sample 2020-2024, prints
side-by-side with canonical and the previous α=-0.5-constant actual-b.
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


def _score_row_principled(row: pd.Series) -> pd.Series:
    """Drop-in replacement for ground._score_row using:
       - probabilities: delta-implied (unchanged)
       - win payoff: actual b = net_credit / max_loss
       - partial multiplier α(b) = (b - 1) / (2b) from uniform-in-strikes
         linear-payoff geometry
       - DKL: probability-only, unchanged
    """
    import historical_probs as hp

    p, q, ro, n = hp.empirical_probs_from_deltas(
        short_delta=row["short_delta"],
        long_delta=row["long_delta"],
    )
    if p is None or p <= 0 or q <= 0 or p + q > 1.0:
        return pd.Series({
            "p": p, "q": q, "ro": ro, "n_samples": n,
            "w_star": None, "G": None, "DKL": None,
        })

    nc = float(row["net_credit"])
    ml = float(row["max_loss"])
    if ml <= 0 or nc <= 0:
        return pd.Series({
            "p": p, "q": q, "ro": ro, "n_samples": n,
            "w_star": None, "G": None, "DKL": None,
        })
    b = nc / ml
    if b >= 1.0:
        # Edge case: credit equals max-loss. α would be 0 (partial = wash).
        a = 0.0
    else:
        a = (b - 1.0) / (2.0 * b)

    # Kelly with payoffs {+b, +αb, −1}
    A = -a * b * b
    B = a * b * b * (p + ro) - b * (p + ro * a + q * (1 + a))
    C = p * b + ro * a * b - q

    if A == 0:
        # α = 0 case: linear FOC, w* = (pb - q) / b (binary Kelly)
        if b == 0:
            return pd.Series({
                "p": p, "q": q, "ro": ro, "n_samples": n,
                "w_star": None, "G": None, "DKL": None,
            })
        w_star = (p * b - q) / b
    else:
        disc = B * B - 4 * A * C
        if disc < 0:
            return pd.Series({
                "p": p, "q": q, "ro": ro, "n_samples": n,
                "w_star": None, "G": None, "DKL": None,
            })
        s = math.sqrt(disc)
        r1 = (-B - s) / (2 * A)
        r2 = (-B + s) / (2 * A)
        cands = [r for r in (r1, r2) if 0 < r < 1]
        if not cands:
            return pd.Series({
                "p": p, "q": q, "ro": ro, "n_samples": n,
                "w_star": None, "G": None, "DKL": None,
            })
        w_star = cands[0]
    w_star = float(np.clip(w_star, 0.01, 0.99))

    def lg(x):
        return math.log(max(x, 1e-10), config.LOG_BASE)

    # Growth with the per-spread b and α
    G = (p  * lg(1.0 + w_star * b) +
         ro * lg(1.0 + w_star * a * b) +
         q  * lg(1.0 - w_star))

    def h(prob):
        return -prob * lg(prob) if prob > 0 else 0.0
    H_chosen  = h(p) + h(ro) + h(q)
    H_uniform = lg(3.0)
    DKL_chosen = max(0.0, H_uniform - H_chosen)

    return pd.Series({
        "p":         p,
        "q":         q,
        "ro":        ro,
        "n_samples": n,
        "w_star":    round(w_star, 4),
        "G":         round(G, 6),
        "DKL":       round(DKL_chosen, 6),
    })


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
    n_weeks   = len(weekly_df)
    total_roi = (final / config.STARTING_BANKROLL - 1) * 100
    ann       = total_roi / (n_weeks / 52) if n_weeks > 0 else 0
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
    print(f"\n== Principled actual-b experiment, qty=1 in-sample {START} → {END} ==\n")
    _setup()
    print("Loading data...")
    df_full       = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]
    print(f"  {len(df):,} option rows\n")

    print(">>> canonical (b=1 implicit, α=-0.5 constant)")
    ground.RANKING_MODE = "GROUND"
    ground.DKL_K = 20.0
    trades_c, weekly_c = backtest.run_backtest(
        df, expiry_prices, pd.DataFrame(), 0,
        top_n=TOP_N, sizing=SIZING,
        use_drift=False, drift_lookup=None,
    )
    _stats("canonical b=1, α=-0.5", weekly_c, trades_c)

    print("\n>>> principled actual-b: per-spread b AND α(b) = (b-1)/(2b)")
    with _patched_scorer():
        trades_p, weekly_p = backtest.run_backtest(
            df, expiry_prices, pd.DataFrame(), 0,
            top_n=TOP_N, sizing=SIZING,
            use_drift=False, drift_lookup=None,
        )
    _stats("actual b, α(b)=(b-1)/(2b)", weekly_p, trades_p)

    return 0


if __name__ == "__main__":
    sys.exit(main())
