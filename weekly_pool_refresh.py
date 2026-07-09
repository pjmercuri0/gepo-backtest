"""Weekly pool refresh — appends new vendor CSVs to master_pool.parquet.

Run every Friday after close:
  python weekly_pool_refresh.py

Process:
  1. Find pool's max ExpirationDate
  2. Read all vendor CSVs with DataDate in [max_exp - 14d, today]
     (14d cushion catches options that recently expired but had option-day
     rows BEFORE pool.max — those rows were filtered out previously because
     expiry_close wasn't yet available)
  3. Apply the pool transform (SP100 filter, DTE 1-4, Mon-Thu entry, Fri
     expiry, expiry_close self-join, ITM outcome)
  4. Drop pool rows where ExpirationDate >= max_exp - 14d (the refresh window)
  5. Concatenate and write back

Typical runtime: 3-5 min for a 2-week refresh window.

Does NOT touch year parquets (output/{year}_sp500_last.parquet).
Those get refreshed monthly via preprocess_empirical.py.
"""
import sys, os, glob, re
import numpy as np
import pandas as pd
sys.path.insert(0, '.')
import config as bt_config

SP100 = set(bt_config.SP100_TICKERS)
POOL_PATH = 'output/master_pool.parquet'
DATA_DIR = bt_config.DATA_DIR
REFRESH_LOOKBACK_DAYS = 14   # re-process the last 2 weeks of expirations

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


def _candidate_files(lo, hi):
    """List vendor CSVs whose DataDate (parsed from filename) is in [lo, hi]."""
    files = sorted(glob.glob(os.path.join(DATA_DIR, 'DG_*', 'Greek_*_OData*.csv')))
    out = []
    for f in files:
        m = re.search(r'Greek_(\d{8})_OData', f)
        if not m:
            continue
        d = pd.Timestamp(m.group(1))
        if lo <= d <= hi:
            out.append((d, f))
    return out


def main():
    if not os.path.exists(POOL_PATH):
        sys.exit(f'ERROR: {POOL_PATH} not found. Run build_production_pool.py first.')

    print(f'Loading pool from {POOL_PATH}...', flush=True)
    pool = pd.read_parquet(POOL_PATH)
    max_exp = pool['ExpirationDate'].max()
    max_data = pool['DataDate'].max()
    print(f'  {len(pool):,} rows  (max DataDate={max_data.date()}, max ExpirationDate={max_exp.date()})')

    lo = max_exp - pd.Timedelta(days=REFRESH_LOOKBACK_DAYS)
    hi = pd.Timestamp.today().normalize()
    print(f'\nScanning vendor CSVs with DataDate in [{lo.date()}, {hi.date()}]...')
    files = _candidate_files(lo, hi)
    if not files:
        print('  No new CSVs found. Pool already up to date.')
        return
    print(f'  found {len(files)} CSVs')

    # Read and concat
    chunks = []
    for i, (d, f) in enumerate(files):
        df = _read_csv(f)
        if df.empty:
            continue
        chunks.append(df)
        if (i+1) % 20 == 0:
            print(f'    read {i+1}/{len(files)}', flush=True)

    if not chunks:
        print('  No usable rows in CSVs.'); return
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
    raw = raw[raw['DTE'].between(0, 8)]   # need DTE=0 for expiry_close self-join
    raw = raw[raw['UnderlyingPrice'] > 0]
    raw = raw[raw['ExpirationDate'].notna()]
    print(f'  after SP100 + DTE filter: {len(raw):,}')

    # Pool transform (same as build_production_pool.process_year_parquet)
    ec = (raw[raw['DataDate']==raw['ExpirationDate']]
          .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice'].first())
    df = raw[(raw['DTE']>=1) & (raw['DTE']<=4)].copy()
    df = df[df['DataDate'].dt.dayofweek.isin([0,1,2,3])]
    df = df[df['ExpirationDate'].dt.dayofweek==4]
    df['expiry_close'] = df.set_index(['Symbol','ExpirationDate']).index.map(ec.get)
    df = df.dropna(subset=['expiry_close','Delta','ImpliedVolatility'])
    df['abs_delta']    = df['Delta'].abs()
    df['itm'] = np.where(
        df['PutCall']=='put',
        df['expiry_close'] < df['StrikePrice'],
        df['expiry_close'] > df['StrikePrice'],
    ).astype(int)
    df['putcall_norm'] = df['PutCall']
    keep = df[['DataDate','ExpirationDate','DTE','putcall_norm','abs_delta','ImpliedVolatility','itm']].copy()
    keep['delta_bucket'] = (keep['abs_delta']*10).astype(int).clip(0,9)
    keep['iv_capped']    = keep['ImpliedVolatility'].clip(upper=3.0)
    print(f'  pool-eligible new rows: {len(keep):,}')

    # Drop pool rows in refresh window, append fresh
    before = len(pool)
    pool = pool[pool['ExpirationDate'] < lo]
    after_drop = len(pool)
    print(f'\nMerging:')
    print(f'  dropped {before - after_drop:,} stale pool rows (ExpirationDate >= {lo.date()})')
    print(f'  appending {len(keep):,} fresh rows')

    combined = pd.concat([pool, keep], ignore_index=True)
    print(f'  combined: {len(combined):,} rows  '
          f'({combined["ExpirationDate"].min().date()} → {combined["ExpirationDate"].max().date()})')
    combined.to_parquet(POOL_PATH, index=False)
    print(f'\nWrote {POOL_PATH}')


if __name__ == '__main__':
    main()
