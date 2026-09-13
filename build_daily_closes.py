"""Seed output/daily_closes.parquet from the vendor year files (SP100 UnderlyingPrice per
Symbol/DataDate = the vendor's end-of-day price) plus expiry-day closes. MERGE only: an
existing store is never overwritten, rows already present are kept.

    python3 build_daily_closes.py            # all year files present under output/
"""
import glob, sys
import pandas as pd
sys.path.insert(0, '.')
import config
from live.closes import append_closes, STORE

paths = sorted(glob.glob('output/20??_sp500_last.parquet')) + sorted(glob.glob('output/2026_sp500_last_oot_combined.parquet'))
sp = set(config.SP100_TICKERS); total = 0
for p in paths:
    d = pd.read_parquet(p, columns=['Symbol', 'DataDate', 'UnderlyingPrice'])
    d = d[d.Symbol.isin(sp) & (d.UnderlyingPrice > 0)]
    d = d.groupby(['Symbol', 'DataDate'], as_index=False)['UnderlyingPrice'].first()
    d = d.rename(columns={'Symbol': 'ticker', 'DataDate': 'date', 'UnderlyingPrice': 'close'})
    n = append_closes(d); total += n
    print(f'{p}: {len(d):,} closes, {n:,} new', flush=True)
print(f'store {STORE}: +{total:,} rows')
