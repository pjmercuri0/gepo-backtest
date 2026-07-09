"""One-shot script: scan all year parquets, compute per-ticker 30d RV,
write output/rv_table.parquet for use by GROUND PROB_BASIS='rv'."""
import sys, glob, pandas as pd
sys.path.insert(0, '.')
from rv_table import compute_rv_table

paths = sorted(glob.glob('output/[0-9][0-9][0-9][0-9]_sp500_last.parquet'))
print(f'Reading {len(paths)} year parquets...', flush=True)
frames = []
for p in paths:
    df = pd.read_parquet(p, columns=['Symbol', 'DataDate', 'UnderlyingPrice'])
    print(f'  {p}: {len(df):,} rows', flush=True)
    frames.append(df)
all_df = pd.concat(frames, ignore_index=True)
print(f'Total: {len(all_df):,} rows; computing RV...', flush=True)

rv = compute_rv_table(all_df)
out = 'output/rv_table.parquet'
rv.to_parquet(out, index=False)
print(f'\nWrote {out}: {len(rv):,} entries '
      f'({rv["rv_30d"].notna().sum():,} with RV, '
      f'{rv["rv_30d"].isna().sum():,} no-history)')
print(f'RV distribution (annualized %):')
print((rv["rv_30d"].dropna() * 100).describe())
