"""Stage 2: choose the short strike by FITTED delta (not vendor delta), then model credit from the fitted surface.
Input: all eligible pairs (vendor caps: OTM<=5%, width<=2.5, OI>=100, bid>0) + per-chain smile fits.
Output: candidate frame with model credit, fitted IVs, fitted deltas, ready for eval_synth.py."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from synth_credit import bs, ncdf, Y_MAX, IV_LO, IV_HI
pairs_path, fits_path, expclose_path, out = sys.argv[1:5]
A = pd.read_parquet(pairs_path)
if 'Symbol' in A.columns:
    A = A.rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date', 'ExpirationDate': 'expiry_date', 'StrikePrice': 'short_strike',
                          'L_StrikePrice': 'long_strike', 'ad': 'd_sh', 'L_ad': 'd_lg', 'UnderlyingPrice': 'entry_price',
                          'ImpliedVolatility': 'IV', 'L_ImpliedVolatility': 'long_IV'})
A['entry_date'] = pd.to_datetime(A.entry_date); A['expiry_date'] = pd.to_datetime(A.expiry_date)
F = pd.read_parquet(fits_path)[['ticker', 'entry_date', 'expiry_date', 'c0', 'c1', 'c2', 'sig0', 'fit_n', 'fit_rmse']].drop_duplicates(['ticker', 'entry_date', 'expiry_date'])
F['entry_date'] = pd.to_datetime(F.entry_date); F['expiry_date'] = pd.to_datetime(F.expiry_date)
A = A.merge(F, on=['ticker', 'entry_date', 'expiry_date'], how='inner')
T = np.clip(A.DTE.values, 1, None) / 365.0; S = A.entry_price.values; is_put = (A.spread_type == 'bull_put').values
for leg in ('short', 'long'):
    K = A[f'{leg}_strike'].values
    y = np.clip(np.log(K / S) / np.sqrt(T) / A.sig0.values, -Y_MAX, Y_MAX)
    iv = np.clip(A.c0 + A.c1 * y + A.c2 * y * y, IV_LO, IV_HI).values
    d1 = (np.log(S / K) + 0.5 * iv * iv * T) / (iv * np.sqrt(T))
    A[f'iv_fit_{leg}'] = iv; A[f'bs_{leg}'] = bs(S, K, iv, T, is_put)
    A[f'dfit_{leg}'] = np.where(is_put, ncdf(-d1), ncdf(d1))
A['model_credit'] = (A.bs_short - A.bs_long).round(4)
A['vendor_d_sh'] = A.d_sh; A['d_sh'] = A.dfit_short; A['d_lg'] = A.dfit_long
A['short_delta'] = np.where(is_put, -A.d_sh, A.d_sh); A['long_delta'] = np.where(is_put, -A.d_lg, A.d_lg)
b = A[A.d_sh.between(0.10, 0.30) & (A.d_lg < A.d_sh) & (A.model_credit > 0.01)].copy(); b['dist'] = (b.d_sh - 0.20).abs()
sel = b.loc[b.groupby(['ticker', 'entry_date', 'expiry_date', 'spread_type'], sort=False)['dist'].idxmin()].copy()
if expclose_path != 'none':
    E = pd.read_parquet(expclose_path); E['expiry_date'] = pd.to_datetime(E.expiry_date)
    sel = sel.merge(E, on=['ticker', 'expiry_date'], how='inner')
sel['width'] = (sel.short_strike - sel.long_strike).abs()
print(f'pairs {len(A):,} -> band candidates {len(sel):,}; med fitted d_sh {sel.d_sh.median():.3f} (vendor {sel.vendor_d_sh.median():.3f}); '
      f'same strike as vendor pick: n/a; med model credit {sel.model_credit.median():.3f} vs vendor mid {sel.net_credit.median():.3f}')
sel.to_parquet(out); print('wrote', out)
