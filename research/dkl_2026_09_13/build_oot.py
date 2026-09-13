# Build 2026 OOT candidate frame WITH market quotes (mirrors Opus's vec.py for IS).
# Caps: OTM<=5%, width<=2.50, OI>=100 both legs, bid>0, mid credit. Band 0.10-0.30 nearest 0.20.
import sys, time
import numpy as np, pandas as pd
sys.path.insert(0, '/Users/mercurio/Downloads/gepo-backtest')
import config as bt_config
T0 = time.time()
SP100 = set(bt_config.SP100_TICKERS); OI = 100; MAXW = 2.50; MAXOTM = 5.0
OUT = '/private/tmp/claude-501/-Users-mercurio-Downloads-gepo-backtest/74cb077b-eb69-4075-bfe1-766ea8fc2c15/scratchpad/oot_sel_10_30.parquet'
td = set(pd.read_csv('/Users/mercurio/Downloads/gepo-backtest/data/spy_us_d.csv', parse_dates=['Date'])['Date'])
d = pd.read_parquet('/Users/mercurio/Downloads/gepo-backtest/output/2026_sp500_last_oot_combined.parquet')
d = d[d.Symbol.isin(SP100)]
d['PutCall'] = d.PutCall.str.lower().str.strip()
ec = (d[d.DataDate == d.ExpirationDate].groupby(['Symbol', 'ExpirationDate'])['UnderlyingPrice'].first()
      .reset_index().rename(columns={'Symbol': 'ticker', 'ExpirationDate': 'expiry_date', 'UnderlyingPrice': 'expiry_close'}))
d['dow'] = d.DataDate.dt.dayofweek; d['exp_dow'] = d.ExpirationDate.dt.dayofweek
d = d[d.dow.isin([0, 1, 2, 3]) & (d.exp_dow == 4) & d.DTE.between(1, 4)]
d = d[(d.LastPrice.astype(float) > 0)]
# SPY csv on this machine ends 2026-05-28; the 2026 parquet runs to 08-21. Use the
# parquet's own weekday DataDates as the trading calendar (matches report_oot_2026 fallback).
d = d[d.DataDate.dt.dayofweek < 5]
print(f'rows after weekday filter {len(d):,}', flush=True)
d = d[d.OpenInterest.fillna(0) >= OI].copy()
d['ad'] = d.Delta.abs(); d['mid'] = (d.BidPrice + d.AskPrice) / 2.0
g = ['Symbol', 'DataDate', 'ExpirationDate', 'PutCall']
d = d.sort_values(g + ['StrikePrice']); gb = d.groupby(g, sort=False)
cols = ['StrikePrice', 'mid', 'BidPrice', 'AskPrice', 'ad', 'ImpliedVolatility', 'OpenInterest']
for c in cols:
    d['lo_' + c] = gb[c].shift(1); d['hi_' + c] = gb[c].shift(-1)
put = d[d.PutCall == 'put'].copy(); put['spread_type'] = 'bull_put'
for c in cols: put['L_' + c] = put['lo_' + c]
cal = d[d.PutCall == 'call'].copy(); cal['spread_type'] = 'bear_call'
for c in cols: cal['L_' + c] = cal['hi_' + c]
x = pd.concat([put, cal], ignore_index=True).dropna(subset=['L_StrikePrice'])
x['width'] = (x.StrikePrice - x.L_StrikePrice).abs()
x['net_credit'] = (x['mid'] - x['L_mid']).round(4)
x['max_loss'] = (x.width - x.net_credit).round(4)
x['otm'] = np.where(x.spread_type == 'bull_put',
                    100 * (x.UnderlyingPrice - x.StrikePrice) / x.UnderlyingPrice,
                    100 * (x.StrikePrice - x.UnderlyingPrice) / x.UnderlyingPrice)
x = x[(x.width > 0) & (x.width <= MAXW) & (x.net_credit > 0) & (x.max_loss > 0)
      & (x.L_OpenInterest.fillna(0) >= OI) & (x.otm <= MAXOTM)
      & (x.BidPrice > 0) & (x.L_ad > 0) & (x.L_ad < x.ad)]
A = x.rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date', 'ExpirationDate': 'expiry_date',
                      'StrikePrice': 'short_strike', 'L_StrikePrice': 'long_strike', 'ad': 'd_sh', 'L_ad': 'd_lg',
                      'UnderlyingPrice': 'entry_price', 'ImpliedVolatility': 'IV', 'L_ImpliedVolatility': 'long_IV'})
A['short_delta'] = np.where(A.spread_type == 'bull_put', -A.d_sh, A.d_sh)
A['long_delta'] = np.where(A.spread_type == 'bull_put', -A.d_lg, A.d_lg)
b = A[A.d_sh.between(0.10, 0.30)].copy(); b['dist'] = (b.d_sh - 0.20).abs()
sel = b.loc[b.groupby(['ticker', 'entry_date', 'expiry_date', 'spread_type'], sort=False)['dist'].idxmin()]
sel = sel[['ticker', 'entry_date', 'expiry_date', 'DTE', 'spread_type', 'entry_price', 'short_strike', 'long_strike',
           'd_sh', 'd_lg', 'IV', 'long_IV', 'BidPrice', 'AskPrice', 'L_BidPrice', 'L_AskPrice',
           'net_credit', 'max_loss', 'width', 'short_delta', 'long_delta', 'otm', 'dist']]
sel = sel.merge(ec, on=['ticker', 'expiry_date'], how='inner')
sel.to_parquet(OUT)
print(f'OOT candidates {len(sel):,}  dates {sel.entry_date.min().date()}..{sel.entry_date.max().date()}  '
      f'med d_sh {sel.d_sh.median():.3f}  [{time.time()-T0:.0f}s]  -> {OUT}')
