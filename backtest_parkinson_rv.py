"""Parkinson RV (10d, from Yahoo OHLC) on the P side of DKL(P_rv || Q_iv).

Only the RV estimator changes: close-to-close 10d -> Parkinson 10d
(sigma^2 = mean of ln(H/L)^2/(4 ln 2), x252, trailing window incl. entry
day, min 5 obs). Q side stays per-leg IV (canon). Sanity check first:
replicate the cached DKL from rv_30d + leg IVs to validate the vectorized
math against ground.py. Both arms restricted to candidates with Parkinson
coverage for a fair comparison. Realization 0.80 x raw_last. Read-only.
"""
import glob, math, os
import numpy as np
import pandas as pd
from math import erf

K_CANON = 10.0
THR_CANON = 0.075
LAST_PCT = 0.80
SPLIT = pd.Timestamp('2025-01-01')
WIN, MIN_OBS = 10, 5

# --- Parkinson table from Yahoo OHLC ---
frames = []
for p in sorted(glob.glob('data/daily_bars_yahoo/*.csv')):
    t = os.path.basename(p)[:-4]
    d = pd.read_csv(p, parse_dates=['date'])
    d = d[(d['high'] > 0) & (d['low'] > 0) & (d['high'] >= d['low'])]
    pk = np.log(d['high'] / d['low']) ** 2 / (4 * np.log(2.0))
    d['rv_park'] = np.sqrt(pk.rolling(WIN, min_periods=MIN_OBS).mean() * 252)
    d['ticker'] = t
    frames.append(d[['ticker', 'date', 'rv_park']])
park = pd.concat(frames, ignore_index=True)
print(f'parkinson table: {park.ticker.nunique()} tickers, {len(park)} rows')

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
df = df.dropna(subset=['p', 'q', 'ro', 'G', 'DKL', 'raw_last', 'expiry_close',
                       'rv_30d', 'entry_price', 'IV', 'long_IV']).copy()
df = df[(df['rv_30d'] > 0) & (df['IV'] > 0) & (df['long_IV'] > 0)]
df['entry_date'] = pd.to_datetime(df['entry_date'])
df = df.merge(park, left_on=['ticker', 'entry_date'], right_on=['ticker', 'date'],
              how='left')
cov = df['rv_park'].notna().mean()
print(f'{len(df)} candidates; parkinson coverage {cov*100:.1f}%')
df = df[df['rv_park'].notna()].copy()
print('rv_park vs rv_30d: ratio med=%.2f  corr=%.3f' % (
    (df.rv_park / df.rv_30d).median(), df.rv_park.corr(df.rv_30d)))


def ncdf(x):
    return 0.5 * (1.0 + np.vectorize(erf)(x / np.sqrt(2.0)))


S = df['entry_price'].to_numpy(float)
KS = df['short_strike'].to_numpy(float)
KL_ = df['long_strike'].to_numpy(float)
T = np.maximum(df['DTE'].to_numpy(float), 1.0) / 365.0
is_put = (df['spread_type'] == 'bull_put').to_numpy()


def nd2_probs(sig_s, sig_l):
    d2s = (np.log(S / KS) - 0.5 * sig_s ** 2 * T) / (sig_s * np.sqrt(T))
    d2l = (np.log(S / KL_) - 0.5 * sig_l ** 2 * T) / (sig_l * np.sqrt(T))
    ps_itm = np.where(is_put, ncdf(-d2s), ncdf(d2s))
    pl_itm = np.where(is_put, ncdf(-d2l), ncdf(d2l))
    pl_itm = np.minimum(pl_itm, ps_itm)
    p = 1.0 - ps_itm
    q = pl_itm
    ro = np.maximum(0.0, ps_itm - pl_itm)
    s = p + q + ro
    return p / s, q / s, ro / s


def dkl3(p1, q1, r1, p2, q2, r2):
    out = np.zeros(len(p1))
    for a, b_ in ((p1, p2), (q1, q2), (r1, r2)):
        m = (a > 0) & (b_ > 0)
        out[m] += a[m] * np.log(a[m] / b_[m])
    return np.maximum(0.0, out)


# sanity: replicate cached DKL from rv_30d vs per-leg IV
iv_s = df['IV'].to_numpy(float)
iv_l = df['long_IV'].to_numpy(float)
rv_cc = np.clip(df['rv_30d'].to_numpy(float), 0.05, 2.0)
Qp, Qq, Qr = nd2_probs(iv_s, iv_l)
Pp, Pq, Pr = nd2_probs(rv_cc, rv_cc)
dkl_chk = dkl3(Pp, Pq, Pr, Qp, Qq, Qr)
diff = np.abs(dkl_chk - df['DKL'].to_numpy())
print(f'DKL replication: med diff {np.median(diff):.5f}, p95 {np.percentile(diff,95):.5f}')

rv_pk = np.clip(df['rv_park'].to_numpy(float), 0.05, 2.0)
Kp, Kq, Kr = nd2_probs(rv_pk, rv_pk)
df['DKL_park'] = dkl3(Kp, Kq, Kr, Qp, Qq, Qr)
print('DKL_park: p50=%.4f p90=%.4f | cached: p50=%.4f p90=%.4f corr=%.3f' % (
    df.DKL_park.quantile(.5), df.DKL_park.quantile(.9),
    df.DKL.quantile(.5), df.DKL.quantile(.9), df.DKL_park.corr(df.DKL)))


def realized_pnl(d):
    credit = LAST_PCT * d['raw_last']
    out = []
    for cr, wd, ss, ls, sp, st in zip(credit, d['width'], d['short_strike'],
                                      d['long_strike'], d['expiry_close'], d['spread_type']):
        if st == 'bull_put':
            intr = min(max(ss - sp, 0.0), wd)
            oc = 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
        else:
            intr = min(max(sp - ss, 0.0), wd)
            oc = 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')
        pnl = (cr - intr) * 100
        if oc == 'PARTIAL' and pnl > 0:
            pnl *= 0.5
        out.append((pnl, oc))
    d = d.copy()
    d[['pnl', 'oc']] = pd.DataFrame(out, index=d.index)
    return d


df = realized_pnl(df)
df['GAMMA0'] = df['G'] / np.exp(K_CANON * df['DKL'])


def select(d, col, thr):
    qd = d[d[col].notna() & (d[col] >= thr)]
    return (qd.sort_values(['entry_date', col], ascending=[True, False])
              .groupby('entry_date').head(5))


def stats(sel):
    if len(sel) == 0:
        return dict(n=0, avg=float('nan'), wr=float('nan'), sh=float('nan'), tot=0.0)
    daily = sel.groupby(sel['realize_date'])['pnl'].sum()
    eq = 10000 + daily.sort_index().cumsum()
    wk = eq.resample('W').last().ffill()
    r = wk.pct_change().dropna()
    sh = r.mean() / r.std() * math.sqrt(52) if r.std() > 0 else float('nan')
    return dict(n=len(sel), avg=sel.pnl.mean(), wr=(sel.oc == 'WIN').mean() * 100,
                sh=sh, tot=sel.pnl.sum())


def show(label, s, extra=''):
    print(f'{label:42s} n={s["n"]:5d} avg=${s["avg"]:7.2f} total=${s["tot"]:9.0f} '
          f'WR={s["wr"]:4.1f}% Sh(wk)={s["sh"]:5.2f}{extra}')


train = df[df['entry_date'] < SPLIT]
selA_tr = select(train, 'GAMMA0', THR_CANON)
n_target = len(selA_tr)
print('\n=== TRAIN 2020-2024 (same filtered universe) ===')
show('canon cc-RV k=10, thr=0.075', stats(selA_tr))

fitted = []
for k in [5, 10, 15, 20, 30]:
    col = f'GP_{k}'
    df[col] = df['G'] / np.exp(k * df['DKL_park'])
    tr = df[df['entry_date'] < SPLIT]
    lo_t, hi_t = 0.0, 1.0
    for _ in range(50):
        m = 0.5 * (lo_t + hi_t)
        if len(select(tr, col, m)) > n_target: lo_t = m
        else: hi_t = m
    thr = 0.5 * (lo_t + hi_t)
    show(f'parkinson k={k:<3} thr={thr:.4f}', stats(select(tr, col, thr)))
    fitted.append((k, thr))

test = df[df['entry_date'] >= SPLIT]
print('\n=== TEST 2025 (train-fitted thresholds) ===')
selA_te = select(test, 'GAMMA0', THR_CANON)
show('canon cc-RV k=10, thr=0.075', stats(selA_te))
keysA = set(zip(selA_te['entry_date'], selA_te['ticker'],
                selA_te['short_strike'], selA_te['spread_type']))
for k, thr in fitted:
    sel = select(test, f'GP_{k}', thr)
    ks = set(zip(sel['entry_date'], sel['ticker'], sel['short_strike'], sel['spread_type']))
    show(f'parkinson k={k:<3} thr={thr:.4f}', stats(sel),
         f'  ovl {len(keysA & ks)}/{len(keysA)}')

df[['entry_date', 'ticker', 'short_strike', 'spread_type', 'G', 'DKL', 'DKL_park',
    'rv_30d', 'rv_park', 'GAMMA0', 'pnl', 'oc']].to_csv('/tmp/parkinson_scores.csv',
                                                        index=False)
print('\nwrote /tmp/parkinson_scores.csv')
