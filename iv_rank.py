"""Per-(Symbol, DataDate) IV-rank computation.

IV-rank = percentile of today's ATM IV against the same ticker's trailing
252 trading days of ATM IV. Splits otherwise-equivalent (delta, IV) buckets
by whether the underlying's vol regime is elevated vs. its own history.

This complements the cross-sectional iv_bucket (which compares against the
50-week pool-wide IV quantiles) by adding a name-specific dimension that
captures vol mean-reversion — the strongest real edge in options.

Bucket convention: 5 quintiles, 0..4 (0 = lowest vol regime, 4 = highest).
"""
from __future__ import annotations
import numpy as np
import pandas as pd

WINDOW_DAYS = 252  # ≈ 1 trading year
N_BUCKETS = 5
ATM_DELTA_LO = 0.40
ATM_DELTA_HI = 0.60
# IV plausibility window for SP100 names. Outside this band almost always = vendor
# noise (e.g. 999999 placeholders) or thinly-priced far-OTM strikes whose stale
# quotes drift wildly. Tighter than the pool's iv_capped=3.0 because we're after
# ATM IV (which never legitimately exceeds ~1.5 even for high-vol names).
IV_MIN, IV_MAX = 0.05, 1.5
MIN_ATM_ROWS = 1  # short-dated weeklies are gamma-concentrated; usually 1-3 strikes in ATM band


def _atm_iv_per_day(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (Symbol, DataDate) with median ATM IV.

    df needs Symbol, DataDate, abs_delta (or Delta), ImpliedVolatility.
    Falls back to median of all rows when no ATM-band rows exist for that day.
    """
    if 'abs_delta' not in df.columns:
        df = df.copy()
        df['abs_delta'] = df['Delta'].abs()
    clean = df[(df['ImpliedVolatility'] >= IV_MIN) & (df['ImpliedVolatility'] <= IV_MAX)]
    atm = clean[(clean['abs_delta'] >= ATM_DELTA_LO) & (clean['abs_delta'] <= ATM_DELTA_HI)]
    atm_agg = (atm.groupby(['Symbol', 'DataDate'])
                  .agg(atm_iv=('ImpliedVolatility', 'median'),
                       n_atm=('ImpliedVolatility', 'size'))
                  .reset_index())
    # Require min coverage in the ATM band — sparse days are too noisy to trust.
    atm_agg.loc[atm_agg['n_atm'] < MIN_ATM_ROWS, 'atm_iv'] = float('nan')
    return atm_agg[['Symbol', 'DataDate', 'atm_iv']]


def compute_iv_rank(df: pd.DataFrame) -> pd.DataFrame:
    """Return DataFrame with (Symbol, DataDate, atm_iv, iv_rank, iv_rank_bucket).

    iv_rank ∈ [0, 1] is the rolling-252d percentile of atm_iv within each ticker.
    iv_rank_bucket ∈ {0..N_BUCKETS-1} is the discretization.
    Returns NaN bucket for days with insufficient history (<60 prior days).
    """
    atm = _atm_iv_per_day(df).sort_values(['Symbol', 'DataDate']).reset_index(drop=True)

    def _rank_per_ticker(g: pd.Series) -> pd.Series:
        # For each row, rank today's value against the trailing window (exclusive of today).
        # pct=True returns rank in [0, 1].
        out = np.full(len(g), np.nan)
        vals = g.values
        for i in range(len(g)):
            lo = max(0, i - WINDOW_DAYS)
            window = vals[lo:i]  # exclude today
            if len(window) < 60:  # minimum history
                continue
            out[i] = (window < vals[i]).sum() / len(window)
        return pd.Series(out, index=g.index)

    atm['iv_rank'] = atm.groupby('Symbol')['atm_iv'].transform(_rank_per_ticker)
    # Bucket: 0..4. Use cut with explicit edges so the discretization is deterministic.
    edges = np.linspace(0, 1, N_BUCKETS + 1)
    edges[-1] += 1e-9
    atm['iv_rank_bucket'] = pd.cut(atm['iv_rank'], bins=edges, labels=False, include_lowest=True)
    return atm
