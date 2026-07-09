"""Helpers for empirical-DKL scoring with rolling 50-week put/call-split lookup.

Used by:
  - backtest scripts (report_three_sizings.py and friends): per-date scoring
    where the empirical table is rebuilt for each entry_date's trailing window
  - live ranker: load the single most-recent snapshot at startup; refresh
    via cron when build_production_pool.py is rerun

Canonical bucket key: (DTE, putcall, delta_bucket, iv_bucket).
Reliability floor: n>=30 per cell, else NaN → falls back to delta-based DKL.
"""
import os
import numpy as np
import pandas as pd

POOL_PATH = 'output/master_pool.parquet'
TRAIL_DAYS = 210  # 30 weeks — canonical 2026-06-03


def load_master_pool(path: str = POOL_PATH) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f'Master pool not found at {path}. Run build_production_pool.py to bootstrap.')
    return pd.read_parquet(path)


def build_window_tables(pool: pd.DataFrame, asof: pd.Timestamp,
                        trail_days: int = TRAIL_DAYS):
    """Build the put/call sub-tables and IV bins for the rolling window ending at asof.

    Returns (tables_dict, iv_bins) where tables_dict has keys 'put' and 'call'.
    Either may be None if the slice is empty for that putcall.
    Returns (None, None) if the full window is empty.

    Bucket key (canonical 2026-06-04): (DTE, delta_bucket, iv_bucket, iv_rank_bucket).
    iv_rank_bucket is the per-ticker rolling-252d IV percentile bucket (0-4),
    populated by build_production_pool. Rows without iv_rank_bucket (insufficient
    history) get bucket = -1 so they form their own cell and don't dilute reliable
    cells.
    """
    lo = asof - pd.Timedelta(days=trail_days)
    sub = pool[(pool['ExpirationDate'] >= lo) & (pool['ExpirationDate'] < asof)]
    if sub.empty:
        return None, None
    iv_bins = sub['iv_capped'].quantile([0, 0.2, 0.4, 0.6, 0.8, 1.0]).values
    iv_bins[-1] += 0.001
    sub = sub.copy()
    sub['iv_bucket'] = pd.cut(sub['iv_capped'], bins=iv_bins, labels=False, include_lowest=True)
    # Use -1 for no-history rows; keeps them separate from the 0..4 ranked buckets.
    if 'iv_rank_bucket' in sub.columns:
        sub['iv_rank_bucket'] = sub['iv_rank_bucket'].fillna(-1).astype(int)
    else:
        sub['iv_rank_bucket'] = -1
    tables = {}
    for pc in ['put', 'call']:
        sub_pc = sub[sub['putcall_norm'] == pc]
        if sub_pc.empty:
            tables[pc] = None
            continue
        agg = sub_pc.groupby(['DTE', 'delta_bucket', 'iv_bucket', 'iv_rank_bucket']).agg(
            n=('itm', 'size'), p_itm=('itm', 'mean')
        ).reset_index()
        agg['p_itm_reliable'] = np.where(agg['n'] >= 30, agg['p_itm'], np.nan)
        tables[pc] = agg
    return tables, iv_bins


def install_tables(tables: dict, iv_bins) -> None:
    """Install (put, call) sub-tables and shared IV bins into historical_probs globals.
    After this, ground.score_candidates uses the empirical lookup for these tables."""
    import historical_probs as hp
    hp._EMPIRICAL_TABLE = tables    # dict form picked up by hp._get_active_table
    hp._EMPIRICAL_IV_BINS = iv_bins


def install_window(pool: pd.DataFrame, asof: pd.Timestamp,
                   trail_days: int = TRAIL_DAYS) -> bool:
    """One-shot: build tables for `asof` and install them. Returns True on success."""
    tables, bins = build_window_tables(pool, asof, trail_days)
    if tables is None:
        return False
    install_tables(tables, bins)
    return True


WINDOW_CACHE_PATH = 'output/empirical_window_cache.pkl'


def install_latest_cached(trail_days: int = TRAIL_DAYS) -> pd.Timestamp:
    """install_latest without the 15M-row pool load when a same-day cache exists.

    The installed artifact (put/call agg tables + iv_bins) is tiny and only
    changes when the master pool file changes or the calendar day rolls over,
    so half-hourly cron firings can skip the 80MB parquet load entirely.
    """
    import pickle
    mtime = os.path.getmtime(POOL_PATH)
    today = pd.Timestamp.today().normalize()
    try:
        with open(WINDOW_CACHE_PATH, 'rb') as f:
            c = pickle.load(f)
        if (c['pool_mtime'] == mtime and c['today'] == str(today.date())
                and c['trail_days'] == trail_days):
            install_tables(c['tables'], c['iv_bins'])
            return c['asof']
    except (FileNotFoundError, EOFError, KeyError, pickle.UnpicklingError):
        pass
    pool = load_master_pool()
    pool_max = pd.Timestamp(pool['ExpirationDate'].max()).normalize() + pd.Timedelta(days=1)
    asof = max(today, pool_max)
    tables, bins = build_window_tables(pool, asof, trail_days)
    if tables is None:
        raise RuntimeError(f'empirical pool window empty as of {asof.date()}')
    install_tables(tables, bins)
    with open(WINDOW_CACHE_PATH, 'wb') as f:
        pickle.dump({'pool_mtime': mtime, 'today': str(today.date()),
                     'trail_days': trail_days, 'asof': asof,
                     'tables': tables, 'iv_bins': bins}, f)
    return asof


def install_latest(pool: pd.DataFrame = None, trail_days: int = TRAIL_DAYS) -> pd.Timestamp:
    """Install the single most-recent snapshot (today's trailing window). For live use.
    Returns the asof date used.

    asof is anchored to the LATER of (today, pool's most recent ExpirationDate + 1)
    so the window always reaches the freshest data even if the pool is stale.
    Use tz-naive timestamp to match pool's ExpirationDate dtype.
    """
    if pool is None:
        pool = load_master_pool()
    today = pd.Timestamp.today().normalize()
    pool_max = pd.Timestamp(pool['ExpirationDate'].max()).normalize() + pd.Timedelta(days=1)
    asof = max(today, pool_max)
    install_window(pool, asof, trail_days)
    return asof
