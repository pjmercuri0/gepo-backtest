"""Histogram of picks per entry day under the canonical config.
Mon/Tue/Thu, k=30, per-day thresholds, top-5 cap.
"""
import sys, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
K_VAL = 30
THRESH_BY_DOW = {0: 0.003, 1: 0.005, 3: 0.010}
ACTIVE_DOWS = [0, 1, 3]
DOW_NAMES = {0:'Mon', 1:'Tue', 3:'Thu'}


def score_year(year):
    df_full = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(ACTIVE_DOWS)]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]
    df = df[df['LastPrice'].astype(float) > 0]
    df = df.copy()
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0

    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = True
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100

    candidates = spreads.build_candidates(df)
    scored = ground.score_candidates(candidates)
    def gnd(r):
        G, DKL = r.get('G'), r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G)-1.0) * math.exp(-K_VAL * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    return scored.dropna(subset=['GROUND']), df['DataDate'].unique()


all_picks = []
all_eligible_dates = []
for year in YEARS:
    print(f'── {year} ──', flush=True)
    scored, eligible_dates = score_year(year)
    s = scored.copy()
    s['entry_dow'] = pd.to_datetime(s['entry_date']).dt.dayofweek
    parts = []
    for dow in ACTIVE_DOWS:
        thr = THRESH_BY_DOW[dow]
        sub = s[(s['entry_dow']==dow) & (s['GROUND']>=thr)]
        top = sub.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
        parts.append(top)
    picks = pd.concat(parts, ignore_index=True)
    all_picks.append(picks)
    all_eligible_dates.extend(eligible_dates)

all_picks = pd.concat(all_picks, ignore_index=True)
picks_per_day = all_picks.groupby('entry_date').size()

# Eligible entry days (Mon/Tue/Thu where data exists). Days with 0 picks get count=0.
eligible_dates = pd.DatetimeIndex(sorted(set(all_eligible_dates)))
counts = picks_per_day.reindex(eligible_dates, fill_value=0)

print(f'\nTotal eligible Mon/Tue/Thu days: {len(eligible_dates):,}')
print(f'Total picks: {int(counts.sum()):,}')
print(f'Mean picks/day: {counts.mean():.2f}')
print(f'Median picks/day: {int(counts.median())}')

print(f'\n══ Histogram of picks per day (2022-2025, Mon/Tue/Thu, k=30, per-day thresh) ══')
print(f'{"bucket":>6} {"count":>6} {"%":>5}')
print('-'*36)
bins = list(range(0, 7))  # 0,1,2,3,4,5+
for b in bins[:-1]:
    n = int((counts == b).sum())
    pct = 100 * n / len(counts)
    bar = '█' * int(pct * 0.5)
    print(f'{b:>6} {n:>6} {pct:>4.1f}%  {bar}')
n_overflow = int((counts > 5).sum())
if n_overflow > 0:
    pct = 100 * n_overflow / len(counts)
    bar = '█' * int(pct * 0.5)
    print(f'{">5":>6} {n_overflow:>6} {pct:>4.1f}%  {bar}')

# Per-DOW breakdown
print(f'\n══ Per-DOW histogram ══')
counts_df = pd.DataFrame({'date': counts.index, 'n': counts.values})
counts_df['dow'] = counts_df['date'].dt.dayofweek
for dow in ACTIVE_DOWS:
    sub = counts_df[counts_df['dow']==dow]
    n_days = len(sub); mean_picks = sub['n'].mean(); median_picks = sub['n'].median()
    print(f'  {DOW_NAMES[dow]}: {n_days} days, mean {mean_picks:.2f} picks, median {int(median_picks)}, '
          f'max {int(sub["n"].max())}')
    for b in range(0, 6):
        n = int((sub['n']==b).sum())
        pct = 100*n/n_days
        bar = '█' * int(pct * 0.4)
        print(f'    {b}: {n:>4} ({pct:>4.1f}%) {bar}')
