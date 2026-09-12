"""Keep output/spread_outcomes_2026.parquet current for 52:10 live scoring.

The 52-expiry window is only as fresh as this population. Run weekly alongside
monthly_pool_refresh.py (both are wired into live/cron_pool_refresh.sh).

SAFETY: this MERGES. It reads the existing file, appends only realized spreads
whose (ticker, entry_date, expiry_date, short_strike, long_strike) is not
already present, and never drops rows. It writes a .bak first. It does not
touch output/spread_outcomes.parquet (the frozen 2020-2025 history) or any
vendor parquet.

A spread is only appended once it has REALIZED — i.e. the vendor file contains
the expiry-day settlement row. Unrealized spreads are skipped, so nothing
forward-looking can enter the table.
"""
import os, shutil, sys
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
ACTIVE = [0, 1, 2, 3]
TARGET = 'output/spread_outcomes_2026.parquet'
KEY = ['ticker', 'entry_date', 'expiry_date', 'short_strike', 'long_strike']
SOURCES = [
    'output/2026_sp500_last_oot_combined.parquet',
    'output/2026_sp500_last_oot_refresh.parquet',
    'output/2026_sp500_last.parquet',
]

bt_config.DELTA_TARGET, bt_config.DELTA_MIN, bt_config.DELTA_MAX = 0.20, 0.10, 0.30
bt_config.CREDIT_BASIS = 'mid'; bt_config.CREDIT_SCALE = 1.0
bt_config.MIN_CREDIT_RATIO = 0.0        # population is ungated, as built
bt_config.MIN_OPEN_INTEREST = 100


def outcome(r):
    sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
    if r['spread_type'] == 'bull_put':
        return 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
    return 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')


def build_from(path):
    d = pd.read_parquet(path)
    d = d[d.Symbol.isin(SP100)]
    d['PutCall'] = d.PutCall.str.lower().str.strip()
    ec = (d[d.DataDate == d.ExpirationDate]
          .groupby(['Symbol', 'ExpirationDate'])['UnderlyingPrice'].first().to_dict())
    d['dow'] = d.DataDate.dt.dayofweek; d['exp_dow'] = d.ExpirationDate.dt.dayofweek
    d = d[d.dow.isin(ACTIVE) & (d.exp_dow == 4) & d.DTE.between(1, 4)]
    d = d[d.LastPrice.astype(float) > 0]
    # Trading-calendar filter only where the calendar actually reaches; a stale
    # spy_us_d.csv must not silently truncate months of population.
    try:
        spy = pd.read_csv(SPY_CSV, parse_dates=['Date'])
        tmax = spy['Date'].max()
        d = d[(d.DataDate > tmax) | (d.DataDate.isin(set(spy['Date'])))]
        if d['DataDate'].max() > tmax:
            print(f'  NOTE: spy_us_d.csv ends {tmax.date()}; accepting vendor dates beyond it')
    except FileNotFoundError:
        pass
    d = d.copy()
    if d.empty: return pd.DataFrame()
    d['AbsDelta'] = d.Delta.abs(); d['MidPrice'] = (d.BidPrice + d.AskPrice) / 2
    spreads.REGIME_FILTER = spreads.GAP_FILTER = spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.EARNINGS_FILTER = spreads.HOLIDAY_FILTER = spreads.REGIME_PER_TICKER = False
    spreads.SLIPPAGE_CENTS = 0.0
    spreads.REGIME_LOOKUP = None
    c = spreads.build_candidates(d)
    if c.empty: return pd.DataFrame()
    c['expiry_close'] = c.apply(lambda r: ec.get((r['ticker'], r['expiry_date'])), axis=1)
    c = c.dropna(subset=['expiry_close']).copy()     # realized only
    if c.empty: return pd.DataFrame()
    c['outcome'] = c.apply(outcome, axis=1)
    c['abs_short_delta'] = c['short_delta'].abs()
    keep = ['ticker', 'entry_date', 'expiry_date', 'DTE', 'spread_type',
            'short_strike', 'long_strike', 'abs_short_delta', 'IV', 'entry_price',
            'expiry_close', 'outcome']
    return c[[k for k in keep if k in c.columns]]


if __name__ == '__main__':
    existing = pd.read_parquet(TARGET) if os.path.exists(TARGET) else pd.DataFrame()
    if not existing.empty:
        existing['entry_date'] = pd.to_datetime(existing['entry_date'])
        existing['expiry_date'] = pd.to_datetime(existing['expiry_date'])
        print(f'existing: {len(existing):,} rows, expiries -> {existing.expiry_date.max().date()}')
    fresh = []
    for p in SOURCES:
        if not os.path.exists(p): continue
        print(f'-- {p} --', flush=True)
        r = build_from(p)
        print(f'   {len(r):,} realized', flush=True)
        if not r.empty: fresh.append(r)
    if not fresh:
        print('nothing to add'); sys.exit(0)
    F = pd.concat(fresh, ignore_index=True)
    F['entry_date'] = pd.to_datetime(F['entry_date']); F['expiry_date'] = pd.to_datetime(F['expiry_date'])
    combined = pd.concat([existing, F], ignore_index=True) if not existing.empty else F
    before = len(combined)
    combined = combined.drop_duplicates(subset=KEY, keep='first').sort_values('expiry_date').reset_index(drop=True)
    added = len(combined) - len(existing)
    if added <= 0:
        print(f'no new rows (dedup {before:,} -> {len(combined):,}); leaving {TARGET} unchanged')
        sys.exit(0)
    if os.path.exists(TARGET):
        shutil.copy2(TARGET, TARGET + '.bak')
        print(f'backed up -> {TARGET}.bak')
    combined.to_parquet(TARGET)
    print(f'wrote {TARGET}: {len(combined):,} rows (+{added:,}), '
          f'expiries -> {combined.expiry_date.max().date()}')
