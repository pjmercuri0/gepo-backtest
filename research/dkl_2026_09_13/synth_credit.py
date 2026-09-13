"""Synthetic credit from a per-chain robust volatility smile.

For every (Symbol, DataDate, ExpirationDate) chain touched by a candidate:
  1. keep quotes with bid>0, ask>bid, IV>0, |y|<=3 where y = ln(K/S)/sqrt(T)
     (puts and calls pooled; by parity they share an IV at r=q=0)
  2. weighted least squares of IV on (1, y, y^2), weight = 1/(1 + rel bid-ask width),
     two robust re-fits dropping residuals beyond 2.5 x MAD
  3. price each candidate leg with Black-Scholes (r=q=0) at the FITTED IV of its strike
  model_credit = BS(short) - BS(long).  Also returns fitted IVs, ATM IV, fit n / rmse.

Usage: python3 synth_credit.py <candidates.parquet> <out.parquet> <year parquet(s)...>
candidates need: ticker, entry_date, expiry_date, DTE, spread_type, entry_price, short_strike, long_strike
"""
import sys, time, math
import numpy as np, pandas as pd
from math import erf

MIN_Q, Y_MAX, IV_LO, IV_HI = 5, 2.5, 0.03, 3.0   # y in ATM-vol sigmas


def ncdf(x):
    return 0.5 * (1.0 + np.vectorize(erf)(x / math.sqrt(2.0)))


def bs(S, K, iv, T, is_put):
    sT = iv * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * iv * iv * T) / sT; d2 = d1 - sT
    call = S * ncdf(d1) - K * ncdf(d2)
    put = call - S + K
    return np.where(is_put, put, call)


def fit_chain(y, iv, w):
    """robust weighted quadratic; returns (coef[3], n_used, rmse) or None"""
    keep = np.ones(len(y), bool)
    coef = None
    for it in range(3):
        if keep.sum() < MIN_Q:
            return None
        X = np.column_stack([np.ones(keep.sum()), y[keep], y[keep] ** 2]) * np.sqrt(w[keep])[:, None]
        try:
            coef = np.linalg.lstsq(X, iv[keep] * np.sqrt(w[keep]), rcond=None)[0]
        except np.linalg.LinAlgError:
            return None
        res = iv - (coef[0] + coef[1] * y + coef[2] * y * y)
        mad = np.median(np.abs(res[keep] - np.median(res[keep]))) * 1.4826 + 1e-6
        new = keep & (np.abs(res) <= 2.5 * mad)
        if new.sum() == keep.sum() or new.sum() < MIN_Q:
            break
        keep = new
    rmse = float(np.sqrt(np.mean(res[keep] ** 2)))
    return coef, int(keep.sum()), rmse


def load_chain(paths, keys):
    """rows of the vendor parquets restricted to the (Symbol, DataDate, ExpirationDate) keys"""
    out = []
    for p in paths:
        d = pd.read_parquet(p, columns=['Symbol', 'DataDate', 'ExpirationDate', 'DTE', 'PutCall', 'StrikePrice',
                                        'BidPrice', 'AskPrice', 'ImpliedVolatility', 'OpenInterest', 'UnderlyingPrice'])
        d = d.merge(keys, on=['Symbol', 'DataDate', 'ExpirationDate'], how='inner')
        out.append(d)
    return pd.concat(out, ignore_index=True)


def synth(C, paths):
    T0 = time.time()
    C = C.copy()
    C['entry_date'] = pd.to_datetime(C.entry_date); C['expiry_date'] = pd.to_datetime(C.expiry_date)
    keys = C[['ticker', 'entry_date', 'expiry_date']].drop_duplicates()
    keys.columns = ['Symbol', 'DataDate', 'ExpirationDate']
    ch = load_chain(paths, keys)
    ch['PutCall'] = ch.PutCall.str.lower().str.strip()
    ch = ch[(ch.BidPrice > 0) & (ch.AskPrice > ch.BidPrice) & (ch.ImpliedVolatility > 0) & (ch.UnderlyingPrice > 0)].copy()
    T = np.clip(ch.DTE.values, 1, None) / 365.0
    ch['lm'] = np.log(ch.StrikePrice / ch.UnderlyingPrice) / np.sqrt(T)
    # first pass: ATM vol = median IV of the 6 strikes nearest the money, per chain
    ch['rank_atm'] = ch.groupby(['Symbol', 'DataDate', 'ExpirationDate'])['lm'].transform(lambda s: s.abs().rank(method='first'))
    atm = (ch[ch.rank_atm <= 6].groupby(['Symbol', 'DataDate', 'ExpirationDate'])['ImpliedVolatility'].median()
           .rename('sig0').reset_index())
    ch = ch.merge(atm, on=['Symbol', 'DataDate', 'ExpirationDate'], how='inner')
    ch['y'] = ch.lm / ch.sig0.clip(IV_LO, IV_HI)          # standardized moneyness in ATM sigmas
    ch = ch[ch.y.abs() <= Y_MAX].copy()
    mid = (ch.BidPrice + ch.AskPrice) / 2
    ch['w'] = 1.0 / (1.0 + (ch.AskPrice - ch.BidPrice) / mid)
    print(f'  chain rows {len(ch):,} for {len(keys):,} chains  [{time.time()-T0:.0f}s]', flush=True)
    fits = {}
    for k, g in ch.groupby(['Symbol', 'DataDate', 'ExpirationDate'], sort=False):
        r = fit_chain(g.y.values, g.ImpliedVolatility.values, g.w.values)
        if r is not None:
            fits[k] = r
    print(f'  fitted {len(fits):,} chains  [{time.time()-T0:.0f}s]', flush=True)
    sig = ch.groupby(['Symbol', 'DataDate', 'ExpirationDate'])['sig0'].first().to_dict()
    F = pd.DataFrame([(k[0], k[1], k[2], c[0], c[1], c[2], n, e, sig[k]) for k, (c, n, e) in fits.items()],
                     columns=['ticker', 'entry_date', 'expiry_date', 'c0', 'c1', 'c2', 'fit_n', 'fit_rmse', 'sig0'])
    C = C.merge(F, on=['ticker', 'entry_date', 'expiry_date'], how='left')
    T = np.clip(C.DTE.values, 1, None) / 365.0; S = C.entry_price.values
    is_put = (C.spread_type == 'bull_put').values
    for leg in ('short', 'long'):
        K = C[f'{leg}_strike'].values
        y = np.clip(np.log(K / S) / np.sqrt(T) / C.sig0.values, -Y_MAX, Y_MAX)   # no extrapolation past the fit window
        C[f'y_{leg}'] = y
        iv = np.clip(C.c0 + C.c1 * y + C.c2 * y * y, IV_LO, IV_HI)
        C[f'iv_fit_{leg}'] = iv
        C[f'bs_{leg}'] = bs(S, K, iv.values, T, is_put)
    C['atm_iv_fit'] = C.c0.clip(IV_LO, IV_HI)
    C['model_credit'] = (C.bs_short - C.bs_long).round(4)
    ok = C.model_credit.notna()
    print(f'  candidates with model credit {ok.mean():.1%}; med model {C.model_credit.median():.3f} vs vendor mid {C.net_credit.median():.3f}; '
          f'med fit_n {C.fit_n.median():.0f} rmse {C.fit_rmse.median():.4f}  [{time.time()-T0:.0f}s]', flush=True)
    return C


if __name__ == '__main__':
    cand, out, paths = sys.argv[1], sys.argv[2], sys.argv[3:]
    C = pd.read_parquet(cand)
    R = synth(C, paths)
    R.to_parquet(out); print('wrote', out, len(R))
