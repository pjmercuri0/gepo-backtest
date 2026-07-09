"""Build the production empirical-history pool.

Generates output/master_pool.parquet — a row per (DataDate, ExpirationDate,
DTE, PutCall, abs_delta, IV, itm) for every SP100 weekly option expiring
on a Friday with DTE 1-4, with realized ITM outcome attached.

This pool feeds the canonical empirical DKL lookup (put/call split) used
by both the backtest and the live ranker.

Run this script:
  - to bootstrap: once after preprocessing all year parquets
  - weekly via cron: to keep the rolling 50w window fresh with newly resolved options

Refresh strategy: scans output/{year}_sp500_last.parquet for all years and
rebuilds from scratch. ~7 min on the 2022-25 dataset.
"""
import sys, os, glob, re, argparse
import numpy as np
import pandas as pd
sys.path.insert(0, '.')
import config as bt_config
from iv_rank import compute_iv_rank

SP100 = set(bt_config.SP100_TICKERS)
OUT_PATH = 'output/master_pool.parquet'
IV_RANK_PATH = 'output/iv_rank.parquet'


def process_year_parquet(path):
    """Read one year parquet, apply pool filters, return slim DataFrame.

    Returns (slim_pool_rows, full_iv_for_rank) — the second is the FULL
    per-(Symbol, DataDate) IV-rank seed data BEFORE DTE/dow filtering,
    so we can compute clean trailing-window percentiles on raw IV history.
    """
    df = pd.read_parquet(path)
    df = df[df['Symbol'].isin(SP100)]
    # Save the full-coverage IV slice BEFORE narrowing to DTE 1-4 weekly Fridays —
    # IV-rank needs the broader history per ticker to compute percentiles.
    iv_rank_seed = (df.dropna(subset=['Delta', 'ImpliedVolatility'])
                      .assign(abs_delta=lambda x: x['Delta'].abs())
                      [['Symbol', 'DataDate', 'abs_delta', 'ImpliedVolatility']])

    ec = (df[df['DataDate']==df['ExpirationDate']]
          .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice'].first())
    df = df[(df['DTE']>=1) & (df['DTE']<=4)].copy()
    df = df[df['DataDate'].dt.dayofweek.isin([0,1,2,3])]
    df = df[df['ExpirationDate'].dt.dayofweek==4]
    df['expiry_close'] = df.set_index(['Symbol','ExpirationDate']).index.map(ec.get)
    df = df.dropna(subset=['expiry_close','Delta','ImpliedVolatility'])
    df['abs_delta'] = df['Delta'].abs()
    df['itm'] = np.where(
        df['PutCall'].str.lower()=='put',
        df['expiry_close'] < df['StrikePrice'],
        df['expiry_close'] > df['StrikePrice'],
    ).astype(int)
    df['putcall_norm'] = df['PutCall'].str.lower()
    keep = df[['Symbol','DataDate','ExpirationDate','DTE','putcall_norm','abs_delta','ImpliedVolatility','itm']].copy()
    keep['delta_bucket'] = (keep['abs_delta']*10).astype(int).clip(0,9)
    keep['iv_capped']    = keep['ImpliedVolatility'].clip(upper=3.0)
    return keep, iv_rank_seed


def build_full():
    year_files = sorted(glob.glob('output/[0-9][0-9][0-9][0-9]_sp500_last.parquet'))
    print(f'Found {len(year_files)} year parquets:')
    for f in year_files: print(f'  {f}')
    frames, iv_seeds = [], []
    for path in year_files:
        m = re.search(r'(\d{4})_sp500', path)
        if not m: continue
        year = int(m.group(1))
        print(f'\n── {year} ──', flush=True)
        keep, iv_seed = process_year_parquet(path)
        print(f'  {len(keep):,} option-day rows, {len(iv_seed):,} IV-rank seed rows', flush=True)
        frames.append(keep)
        iv_seeds.append(iv_seed)
    pool = pd.concat(frames, ignore_index=True)
    all_iv_seed = pd.concat(iv_seeds, ignore_index=True)

    print(f'\nComputing per-ticker IV-rank (rolling 252d)...', flush=True)
    iv_rank = compute_iv_rank(all_iv_seed)
    iv_rank.to_parquet(IV_RANK_PATH, index=False)
    print(f'  IV-rank entries: {len(iv_rank):,} '
          f'({iv_rank["iv_rank_bucket"].notna().sum():,} with rank, '
          f'{iv_rank["iv_rank_bucket"].isna().sum():,} no-history)')

    pool = pool.merge(iv_rank[['Symbol', 'DataDate', 'iv_rank_bucket']],
                      on=['Symbol', 'DataDate'], how='left')
    print(f'\nTotal pool: {len(pool):,} rows  '
          f'({pool["iv_rank_bucket"].notna().sum():,} with iv_rank, '
          f'{pool["iv_rank_bucket"].isna().sum():,} no-rank)')
    print(f'Date range: {pool["ExpirationDate"].min().date()} → {pool["ExpirationDate"].max().date()}')
    pool.to_parquet(OUT_PATH, index=False)
    print(f'Wrote {OUT_PATH}')
    print(f'Wrote {IV_RANK_PATH}')


def build_incremental(year):
    """Read existing pool, drop rows where year(DataDate) == year,
    process just that year's parquet, append, write back.

    IV-rank is rebuilt fully from the new pool (cheap once per ticker)."""
    path = f'output/{year}_sp500_last.parquet'
    if not os.path.exists(path):
        sys.exit(f'  ERROR: {path} not found')
    if not os.path.exists(OUT_PATH):
        sys.exit(f'  ERROR: {OUT_PATH} not found — run a full build first')

    print(f'Loading existing pool from {OUT_PATH}...', flush=True)
    pool = pd.read_parquet(OUT_PATH)
    before = len(pool)
    pool = pool[pool['DataDate'].dt.year != year]
    after_drop = len(pool)
    print(f'  {before:,} → {after_drop:,} rows after dropping year={year}', flush=True)

    print(f'\nProcessing {path}...', flush=True)
    new_rows, _ = process_year_parquet(path)
    print(f'  {len(new_rows):,} new rows for {year}', flush=True)

    combined = pd.concat([pool, new_rows], ignore_index=True)
    print(f'\nCombined pool: {len(combined):,} rows '
          f'({combined["ExpirationDate"].min().date()} → {combined["ExpirationDate"].max().date()})')
    combined.to_parquet(OUT_PATH, index=False)
    print(f'Wrote {OUT_PATH}')
    print(f'  WARNING: iv_rank.parquet was NOT rebuilt. Run full build for refresh.')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--incremental-year', type=int, default=None,
                   help='Append/replace just this year\'s rows; skip rebuilding prior years.')
    args = p.parse_args()
    if args.incremental_year is not None:
        build_incremental(args.incremental_year)
    else:
        build_full()
