"""Weekly Friday pool refresh from current-month vendor folder.

Workflow you commit to: every Friday, re-upload the entire current month's
DG_YYYYMonth/ folder (it has all month-to-date data). This script then:

  1. Locates this month's DG folder (and last month's, as a safety net for
     month-boundary refreshes)
  2. Processes all CSVs in those folders
  3. Drops any pool rows in the same ExpirationDate range
  4. Appends fresh rows

Run:  python monthly_pool_refresh.py

Typical runtime: 5-10 min depending on how many days have accumulated.

Does NOT touch year parquets — those refresh monthly via preprocess_empirical.py
when the user decides.
"""
import sys, os, glob, re
import numpy as np
import pandas as pd
sys.path.insert(0, '.')
import config as bt_config

SP100 = set(bt_config.SP100_TICKERS)
POOL_PATH = 'output/master_pool.parquet'
DATA_DIR = bt_config.DATA_DIR

REQUIRED_COLS = [
    'Symbol', 'ExpirationDate', 'AskPrice', 'BidPrice',
    'StrikePrice', 'PutCall', 'Delta', 'ImpliedVolatility',
    'UnderlyingPrice', 'OpenInterest', 'DataDate',
]


def _read_csv(path):
    try:
        return pd.read_csv(path, dtype=str, usecols=REQUIRED_COLS)
    except Exception:
        try:
            return pd.read_csv(path, dtype=str, usecols=REQUIRED_COLS,
                               engine='python', on_bad_lines='skip')
        except Exception as e:
            print(f'  skip {os.path.basename(path)}: {e}')
            return pd.DataFrame()


def _month_folder(year, month):
    """Return path to DG_YYYYMonth folder, or None if it doesn't exist."""
    month_name = pd.Timestamp(year, month, 1).strftime('%B')   # e.g. "June"
    folder = os.path.join(DATA_DIR, f'DG_{year}{month_name}')
    return folder if os.path.isdir(folder) else None


def main():
    if not os.path.exists(POOL_PATH):
        sys.exit(f'ERROR: {POOL_PATH} not found. Run build_production_pool.py first.')

    print(f'Loading pool from {POOL_PATH}...', flush=True)
    pool = pd.read_parquet(POOL_PATH)
    max_exp = pool['ExpirationDate'].max()
    print(f'  {len(pool):,} rows  (max ExpirationDate={max_exp.date()})')

    # Process current month + previous month for safety at month boundaries
    today = pd.Timestamp.today()
    last  = today - pd.DateOffset(months=1)
    folders = [f for f in [_month_folder(last.year, last.month),
                            _month_folder(today.year, today.month)] if f]
    if not folders:
        sys.exit(f'  No DG folder found for {today.strftime("%B %Y")} or {last.strftime("%B %Y")}')

    print(f'\nProcessing folders:')
    for f in folders: print(f'  {f}')

    files = []
    for folder in folders:
        files += sorted(glob.glob(os.path.join(folder, 'Greek_*_OData*.csv')))
    print(f'  {len(files)} CSVs')

    chunks = []
    for i, f in enumerate(files):
        df = _read_csv(f)
        if not df.empty:
            chunks.append(df)
        if (i+1) % 20 == 0:
            print(f'    read {i+1}/{len(files)}', flush=True)
    if not chunks:
        print('  No usable rows.'); return
    raw = pd.concat(chunks, ignore_index=True)
    print(f'  raw rows: {len(raw):,}')

    # Type coercion + filter
    for col in ['AskPrice','BidPrice','StrikePrice','Delta','ImpliedVolatility',
                'UnderlyingPrice','OpenInterest']:
        raw[col] = pd.to_numeric(raw[col], errors='coerce')
    raw['DataDate']       = pd.to_datetime(raw['DataDate'], errors='coerce')
    raw['ExpirationDate'] = pd.to_datetime(raw['ExpirationDate'], errors='coerce')
    raw['PutCall']        = raw['PutCall'].str.lower().str.strip()
    raw['DTE']            = (raw['ExpirationDate'] - raw['DataDate']).dt.days
    raw = raw[raw['Symbol'].isin(SP100)]
    raw = raw[raw['DTE'].between(0, 8)]
    raw = raw[raw['UnderlyingPrice'] > 0]
    raw = raw[raw['ExpirationDate'].notna()]
    print(f'  after SP100 + DTE 0-8 filter: {len(raw):,}')

    # Pool transform (matches build_production_pool.process_year_parquet)
    ec = (raw[raw['DataDate']==raw['ExpirationDate']]
          .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice'].first())
    df = raw[(raw['DTE']>=1) & (raw['DTE']<=4)].copy()
    df = df[df['DataDate'].dt.dayofweek.isin([0,1,2,3])]
    df = df[df['ExpirationDate'].dt.dayofweek==4]
    df['expiry_close'] = df.set_index(['Symbol','ExpirationDate']).index.map(ec.get)
    df = df.dropna(subset=['expiry_close','Delta','ImpliedVolatility'])
    df['abs_delta'] = df['Delta'].abs()
    df['itm'] = np.where(
        df['PutCall']=='put',
        df['expiry_close'] < df['StrikePrice'],
        df['expiry_close'] > df['StrikePrice'],
    ).astype(int)
    df['putcall_norm'] = df['PutCall']
    keep = df[['DataDate','ExpirationDate','DTE','putcall_norm','abs_delta','ImpliedVolatility','itm']].copy()
    keep['delta_bucket'] = (keep['abs_delta']*10).astype(int).clip(0,9)
    keep['iv_capped']    = keep['ImpliedVolatility'].clip(upper=3.0)
    print(f'  pool-eligible: {len(keep):,}')

    if keep.empty:
        print('  nothing to merge.'); return

    # Drop overlapping range from existing pool, append new rows
    drop_lo = keep['ExpirationDate'].min()
    drop_hi = keep['ExpirationDate'].max()
    print(f'\nMerging (ExpirationDate range {drop_lo.date()} → {drop_hi.date()}):')
    before = len(pool)
    pool = pool[(pool['ExpirationDate'] < drop_lo) | (pool['ExpirationDate'] > drop_hi)]
    print(f'  dropped {before - len(pool):,} stale rows in range')
    print(f'  appending {len(keep):,} fresh rows')

    combined = pd.concat([pool, keep], ignore_index=True)
    print(f'  combined: {len(combined):,} rows  '
          f'({combined["ExpirationDate"].min().date()} → {combined["ExpirationDate"].max().date()})')
    combined.to_parquet(POOL_PATH, index=False)
    print(f'\nWrote {POOL_PATH}')


if __name__ == '__main__':
    main()
