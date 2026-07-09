"""Per-(Symbol, DataDate) realized volatility lookup.

RV_30d = stdev(daily log returns over last 30 trading days) × √252.

Used to compute "RV-implied (p, q, ro)" in GROUND when PROB_BASIS='rv',
as an alternative to delta-implied probabilities. Since IV > RV on average
(variance risk premium), RV-implied probabilities give higher win-rate
estimates → higher G → more permissive selection that bets the VRP gap
will persist (which it does ~80% of the time).
"""
from __future__ import annotations
import numpy as np
import pandas as pd

WINDOW_DAYS = 10  # tenor-matched to DTE 1-4 weekly strategy (2026-06-05)
MIN_OBS = 5       # require ≥N daily returns to report; else NaN


def compute_rv_table(df: pd.DataFrame) -> pd.DataFrame:
    """Return DataFrame with (Symbol, DataDate, rv_30d) — annualized realized vol.

    df needs Symbol, DataDate, UnderlyingPrice.
    Vectorized — runs in seconds even on the full multi-year option universe.
    """
    spot = (df[['Symbol', 'DataDate', 'UnderlyingPrice']]
              .dropna(subset=['UnderlyingPrice'])
              .drop_duplicates(subset=['Symbol', 'DataDate'])
              .sort_values(['Symbol', 'DataDate'])
              .reset_index(drop=True))
    spot['log_ret'] = (spot.groupby('Symbol')['UnderlyingPrice']
                          .transform(lambda s: np.log(s / s.shift(1))))
    spot['rv_30d'] = (spot.groupby('Symbol')['log_ret']
                         .transform(lambda s: s.rolling(WINDOW_DAYS, min_periods=MIN_OBS).std()
                                              * np.sqrt(252)))
    return spot[['Symbol', 'DataDate', 'rv_30d']]
