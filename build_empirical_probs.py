"""Build empirical P(ITM) lookup table from 2022-2024 (training years).
Holdout: 2025 (test year — NOT used to build the table).

Buckets: (DTE_int, |delta|_decile, IV_quintile)
For each option-day row in training data, determine whether option was ITM at expiry.
Aggregate: P(ITM) per bucket.
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config

SP100 = set(bt_config.SP100_TICKERS)
TRAIN_YEARS = [2022, 2023, 2024]
OUT_PATH = 'output/empirical_probs.parquet'

frames = []
for year in TRAIN_YEARS:
    print(f'── loading {year} ──', flush=True)
    df = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df = df[df['Symbol'].isin(SP100)]
    # Build expiry close lookup
    ec = (df[df['DataDate']==df['ExpirationDate']]
          .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
          .first())
    # Restrict to DTE 1-4 candidates (matches our strategy universe)
    df = df[(df['DTE']>=1)&(df['DTE']<=4)].copy()
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
    frames.append(df[['DTE','abs_delta','ImpliedVolatility','itm']])
    print(f'  {len(df):,} option-day rows', flush=True)

train = pd.concat(frames, ignore_index=True)
print(f'\nTotal training rows: {len(train):,}')

# Bucket: DTE int 1..4, abs_delta decile [0,1)/10, IV quintile [0,1)/5
train['delta_bucket'] = (train['abs_delta'] * 10).astype(int).clip(0, 9)  # 0..9
# Cap IV at 3.0 (300%) to limit outlier influence
train['iv_capped']    = train['ImpliedVolatility'].clip(upper=3.0)
# Use quintiles from training distribution
iv_bins = train['iv_capped'].quantile([0, 0.2, 0.4, 0.6, 0.8, 1.0]).values
iv_bins[-1] += 0.001  # ensure right-edge inclusion
train['iv_bucket'] = pd.cut(train['iv_capped'], bins=iv_bins, labels=False, include_lowest=True)

agg = train.groupby(['DTE','delta_bucket','iv_bucket']).agg(
    n=('itm','size'),
    p_itm=('itm','mean')
).reset_index()

print(f'\nBuckets: {len(agg):,} total')
print(f'  Cells with n>=30: {(agg["n"]>=30).sum()}')
print(f'  Cells with n<30:  {(agg["n"]<30).sum()}')

# For each bucket: p_itm IF n>=30, else NaN (will fall back to delta at prediction time)
agg['p_itm_reliable'] = np.where(agg['n']>=30, agg['p_itm'], np.nan)

# Save the bin edges too so prediction-time bucketing matches
agg.attrs['iv_bins'] = iv_bins
agg.to_parquet(OUT_PATH)
# Save bins separately as a small file
np.save('output/empirical_iv_bins.npy', iv_bins)

print(f'\nWrote {OUT_PATH}')
print(f'\nSample (DTE=1, all buckets):')
print(agg[agg['DTE']==1].to_string(index=False))
