"""b=1 normalized scoring under the new clean filters (OI=100, MAX_CR=1.0).

Tests whether the original canonical scoring (b=1, α=-0.5, k=20) survives
on the filtered data, vs. the new principled actual-b at k=10. If b=1
clean is meaningfully better, we should revert the scoring change.
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


def _score_row_b1(row: pd.Series) -> pd.Series:
    """The OLD canonical scoring: normalized payoffs {+1, +α, -1}, α=-0.5."""
    import historical_probs as hp

    p, q, ro, n = hp.empirical_probs_from_deltas(
        short_delta=row["short_delta"],
        long_delta=row["long_delta"],
    )
    if p is None or p <= 0 or q <= 0 or p + q > 1.0:
        return pd.Series({"p": p, "q": q, "ro": ro, "n_samples": n,
                          "w_star": None, "G": None, "DKL": None})

    a = config.ALPHA  # -0.5
    numer_a      = a*p - a*q - p - q
    discriminant = numer_a**2 + 4*a*(p - q + a - a*p - a*q)
    if discriminant < 0 or a == 0:
        return pd.Series({"p": p, "q": q, "ro": ro, "n_samples": n,
                          "w_star": None, "G": None, "DKL": None})
    w_star = (numer_a + math.sqrt(discriminant)) / (2.0 * a)
    w_star = float(np.clip(w_star, 0.01, 0.99))

    def lg(x):
        return math.log(max(x, 1e-10), config.LOG_BASE)

    G = (p  * lg(1.0 + w_star) +
         ro * lg(1.0 + a * w_star) +
         q  * lg(1.0 - w_star))

    def h(prob):
        return -prob * lg(prob) if prob > 0 else 0.0
    H_chosen  = h(p) + h(ro) + h(q)
    H_uniform = lg(3.0)
    DKL_chosen = max(0.0, H_uniform - H_chosen)

    return pd.Series({
        "p": p, "q": q, "ro": ro, "n_samples": n,
        "w_star": round(w_star, 4),
        "G":      round(G, 6),
        "DKL":    round(DKL_chosen, 6),
    })


@contextmanager
def _patched_scorer():
    original = ground._score_row
    ground._score_row = _score_row_b1
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
    if weekly_df.empty or trades_df.empty or "contracts" not in trades_df.columns:
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
    print(f"  {label:46s}  trades={len(trades_df):4d}  final=${final:>10,.0f}  "
          f"Sharpe={sharpe:.2f}  yield={yld:5.2f}%  DD={dd:5.1f}%")


def main() -> int:
    print(f"\n== b=1 normalized vs actual-b under clean filters, qty=1 {START} → {END} ==")
    print(f"   (OI=100, MAX_CREDIT_RATIO=1.0)\n")
    _setup()
    print("Loading data...")
    df_full       = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]
    print(f"  {len(df):,} option rows\n")

    ground.RANKING_MODE = "GROUND"

    # 1) b=1 normalized at k=20 (the OLD canonical scoring)
    print(">>> b=1 normalized (OLD canonical: α=-0.5, k=20)")
    ground.DKL_K = 20.0
    with _patched_scorer():
        trades_b1, weekly_b1 = backtest.run_backtest(
            df, expiry_prices, pd.DataFrame(), 0,
            top_n=TOP_N, sizing=SIZING,
            use_drift=False, drift_lookup=None,
        )
    _stats("b=1 normalized, k=20", weekly_b1, trades_b1)

    # 2) actual-b at k=10 (NEW canonical scoring at clean filters)
    print("\n>>> actual-b principled (NEW canonical: α(b), k=10)")
    ground.DKL_K = 10.0
    trades_p, weekly_p = backtest.run_backtest(
        df, expiry_prices, pd.DataFrame(), 0,
        top_n=TOP_N, sizing=SIZING,
        use_drift=False, drift_lookup=None,
    )
    _stats("actual-b α(b), k=10", weekly_p, trades_p)

    return 0


if __name__ == "__main__":
    sys.exit(main())
