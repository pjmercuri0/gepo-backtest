"""Direct 3-state empirical triple: measure WIN / PARTIAL / LOSS frequencies
from realized historical SPREADS, instead of deriving ro by subtracting two
single-leg P(ITM) lookups.

Why: the canonical table stores single-leg P(ITM) keyed on
(DTE, delta_bucket, iv_bucket, iv_rank_bucket) and builds the triple as
    p  = 1 - P(short ITM)
    q  =     P(long  ITM)
    ro =     P(short ITM) - P(long ITM)
At delta-20 the two legs sit a median 0.089 delta apart — narrower than the
0.1-wide delta_bucket — so 19.5% of candidates get the SAME p_itm for both legs
and ro collapses to exactly 0. That is a resolution artifact, not a measurement:
the pin zone is never counted at all.

This builds the honest version. For every historical delta-20 spread we know
where spot actually finished relative to the two strikes, so WIN / PARTIAL /
LOSS are counted directly and ro=0 only when no pin actually occurred.

Population: all SP100 delta-20 spreads, Mon-Thu entries, Friday expiry, DTE 1-4,
OI>=100, NO credit-ratio gate (maximises sample; same structure as what we
trade, just unfiltered). Writes output/spread_outcomes.parquet with one row per
realized spread, to be windowed causally at scoring time.
"""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
ACTIVE = [0, 1, 2, 3]
OUT = 'output/spread_outcomes.parquet'

bt_config.DELTA_TARGET, bt_config.DELTA_MIN, bt_config.DELTA_MAX = 0.20, 0.10, 0.30
bt_config.CREDIT_BASIS = 'mid'; bt_config.CREDIT_SCALE = 1.0
bt_config.MIN_CREDIT_RATIO = 0.0      # gate OFF: we want the full population
bt_config.MIN_OPEN_INTEREST = 100


def build_year(year, ivr, rvt, tdays):
    d = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    d = d[d.Symbol.isin(SP100)]
    d['PutCall'] = d.PutCall.str.lower().str.strip()
    ec = (d[d.DataDate == d.ExpirationDate]
          .groupby(['Symbol', 'ExpirationDate'])['UnderlyingPrice'].first().to_dict())
    d['dow'] = d.DataDate.dt.dayofweek; d['exp_dow'] = d.ExpirationDate.dt.dayofweek
    d = d[d.dow.isin(ACTIVE) & (d.exp_dow == 4) & d.DTE.between(1, 4)]
    d = d[(d.LastPrice.astype(float) > 0) & d.DataDate.isin(tdays)].copy()
    if d.empty: return pd.DataFrame()
    d['AbsDelta'] = d.Delta.abs(); d['MidPrice'] = (d.BidPrice + d.AskPrice) / 2
    d = d.merge(ivr[['Symbol', 'DataDate', 'iv_rank_bucket']], on=['Symbol', 'DataDate'], how='left')
    d = d.merge(rvt[['Symbol', 'DataDate', 'rv_30d']], on=['Symbol', 'DataDate'], how='left')
    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = spreads.GAP_FILTER = spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.EARNINGS_FILTER = spreads.HOLIDAY_FILTER = spreads.REGIME_PER_TICKER = False
    spreads.SLIPPAGE_CENTS = 0.0
    c = spreads.build_candidates(d)
    if c.empty: return pd.DataFrame()
    c['expiry_close'] = c.apply(lambda r: ec.get((r['ticker'], r['expiry_date'])), axis=1)
    c = c.dropna(subset=['expiry_close']).copy()
    if c.empty: return pd.DataFrame()

    # THE DIRECT MEASUREMENT: where did spot actually finish?
    def outcome(r):
        sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
        if r['spread_type'] == 'bull_put':
            return 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
        return 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')
    c['outcome'] = c.apply(outcome, axis=1)

    c['abs_short_delta'] = c['short_delta'].abs()
    c['putcall_norm'] = np.where(c['spread_type'] == 'bull_put', 'put', 'call')
    keep = ['ticker', 'entry_date', 'expiry_date', 'DTE', 'spread_type', 'putcall_norm',
            'short_strike', 'long_strike', 'abs_short_delta', 'IV', 'long_IV',
            'iv_rank_bucket', 'entry_price', 'expiry_close', 'outcome']
    return c[[k for k in keep if k in c.columns]]


if __name__ == '__main__':
    if os.path.exists(OUT):
        print(f'{OUT} already exists — refusing to overwrite. Delete it first if you '
              f'intend to rebuild.')
        sys.exit(0)
    ivr = pd.read_parquet('output/iv_rank.parquet'); ivr['DataDate'] = pd.to_datetime(ivr['DataDate'])
    rvt = pd.read_parquet('output/rv_table.parquet'); rvt['DataDate'] = pd.to_datetime(rvt['DataDate'])
    tdays = set(pd.read_csv(SPY_CSV, parse_dates=['Date'])['Date'])
    parts = []
    for y in [2020, 2021, 2022, 2023, 2024, 2025]:
        print(f'-- {y} --', flush=True)
        r = build_year(y, ivr, rvt, tdays)
        print(f'   {len(r):,} realized spreads', flush=True)
        if not r.empty: parts.append(r)
    T = pd.concat(parts, ignore_index=True)
    T.to_parquet(OUT)
    print(f'\nwrote {OUT}: {len(T):,} realized spreads')
    print(f'  date range {T.entry_date.min()} -> {T.entry_date.max()}')
    print('\noutcome mix (this is what the table will measure directly):')
    print((100 * T.outcome.value_counts(normalize=True)).round(2).to_string())
    print(f'\nspreads per 4-expiry window (approx): {len(T) / (T.expiry_date.nunique()/4):,.0f}')
    print(f'distinct expiries: {T.expiry_date.nunique()}')
