"""Range-based estimators INSIDE the G_rv variant: Parkinson 10d and
Yang-Zhang 10d (from Yahoo OHLC) vs close-to-close, each used coherently in
both G's Kelly probs and DKL's P side. Controls: canon (delta probs) and
G_rv on cached rv_30d. k=10, count-matched, 0.80 x raw_last. Read-only.
"""
import glob, math, os
import numpy as np
import pandas as pd
from math import erf

K_CANON = 10.0
THR_CANON = 0.075
LAST_PCT = 0.80
SPLIT = pd.Timestamp('2025-01-01')

frames = []
for p in sorted(glob.glob('data/daily_bars_yahoo/*.csv')):
    t = os.path.basename(p)[:-4]
    d = pd.read_csv(p, parse_dates=['date']).sort_values('date')
    d = d[(d['high'] > 0) & (d['low'] > 0) & (d['close'] > 0) & (d['open'] > 0)
          & (d['high'] >= d['low'])]
    r = np.log(d['close'] / d['close'].shift(1))
    d['rv_cc10'] = r.rolling(10, min_periods=5).std() * np.sqrt(252)
    pk = np.log(d['high'] / d['low']) ** 2 / (4 * np.log(2.0))
    d['rv_pk10'] = np.sqrt(pk.rolling(10, min_periods=5).mean() * 252)
    o = np.log(d['open'] / d['close'].shift(1))
    c = np.log(d['close'] / d['open'])
    rs = (np.log(d['high'] / d['close']) * np.log(d['high'] / d['open'])
          + np.log(d['low'] / d['close']) * np.log(d['low'] / d['open']))
    n = 10
    k_yz = 0.34 / (1.34 + (n + 1) / (n - 1))
    d['rv_yz10'] = np.sqrt((o.rolling(n, min_periods=5).var()
                            + k_yz * c.rolling(n, min_periods=5).var()
                            + (1 - k_yz) * rs.rolling(n, min_periods=5).mean()
                            ).clip(lower=0) * 252)
    d['ticker'] = t
    frames.append(d[['ticker', 'date', 'rv_cc10', 'rv_pk10', 'rv_yz10']])
tab = pd.concat(frames, ignore_index=True)

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
df = df.dropna(subset=['p', 'q', 'ro', 'G', 'DKL', 'raw_last', 'expiry_close',
                       'rv_30d', 'entry_price', 'IV', 'long_IV',
                       'net_credit', 'max_loss']).copy()
df = df[(df['rv_30d'] > 0) & (df['IV'] > 0) & (df['long_IV'] > 0)
        & (df['max_loss'] > 0) & (df['net_credit'] > 0)]
df['entry_date'] = pd.to_datetime(df['entry_date'])
df = df.merge(tab, left_on=['ticker', 'entry_date'], right_on=['ticker', 'date'],
              how='left')
need = ['rv_cc10', 'rv_pk10', 'rv_yz10']
df = df[df[need].notna().all(axis=1)].copy()
print(f'{len(df)} candidates with estimator coverage')

S = df['entry_price'].to_numpy(float)
KS = df['short_strike'].to_numpy(float)
KL_ = df['long_strike'].to_numpy(float)
T = np.maximum(df['DTE'].to_numpy(float), 1.0) / 365.0
is_put = (df['spread_type'] == 'bull_put').to_numpy()
b = df['net_credit'].to_numpy(float) / df['max_loss'].to_numpy(float)
a_par = np.where(b >= 1.0, 0.0, (b - 1.0) / (2.0 * b))


def ncdf(x):
    return 0.5 * (1.0 + np.vectorize(erf)(x / np.sqrt(2.0)))


def nd2_legs(sig_s, sig_l):
    d2s = (np.log(S / KS) - 0.5 * sig_s ** 2 * T) / (sig_s * np.sqrt(T))
    d2l = (np.log(S / KL_) - 0.5 * sig_l ** 2 * T) / (sig_l * np.sqrt(T))
    A = np.where(is_put, ncdf(-d2s), ncdf(d2s))
    B = np.where(is_put, ncdf(-d2l), ncdf(d2l))
    B = np.minimum(B, A)
    p = 1.0 - A
    q = B
    ro = np.maximum(0.0, A - B)
    s = p + q + ro
    return p / s, q / s, ro / s


def kelly_G(p, q, ro):
    A = -a_par * b * b
    B = a_par * b * b * (p + ro) - b * (p + ro * a_par + q * (1 + a_par))
    C = p * b + ro * a_par * b - q
    w_lin = np.where(b > 0, (p * b - q) / np.maximum(b, 1e-12), np.nan)
    disc = B * B - 4 * A * C
    with np.errstate(invalid='ignore', divide='ignore'):
        s = np.sqrt(np.maximum(disc, 0.0))
        r1 = (-B - s) / (2 * A)
        r2 = (-B + s) / (2 * A)
    w = np.where((r1 > 0) & (r1 < 1), r1, np.where((r2 > 0) & (r2 < 1), r2, np.nan))
    w = np.where(A == 0, w_lin, w)
    w = np.where(disc < 0, np.nan, w)
    w = np.clip(w, 0.01, 0.99)
    with np.errstate(invalid='ignore'):
        G = (p * np.log(np.maximum(1.0 + w * b, 1e-10))
             + ro * np.log(np.maximum(1.0 + w * a_par * b, 1e-10))
             + q * np.log(np.maximum(1.0 - w, 1e-10)))
    return np.where(np.isnan(w), np.nan, G)


def dkl3(p1, q1, r1, p2, q2, r2):
    out = np.zeros(len(p1))
    for x, y in ((p1, p2), (q1, q2), (r1, r2)):
        m = (x > 0) & (y > 0)
        out[m] += x[m] * np.log(x[m] / y[m])
    return np.maximum(0.0, out)


Qp, Qq, Qr = nd2_legs(df['IV'].to_numpy(float), df['long_IV'].to_numpy(float))

arms = {}
rv_c = np.clip(df['rv_30d'].to_numpy(float), 0.05, 2.0)
Pp, Pq, Pr = nd2_legs(rv_c, rv_c)
df['SC_cached'] = kelly_G(Pp, Pq, Pr) / np.exp(K_CANON * df['DKL'])
arms['G_rv cached cc10'] = 'SC_cached'

for name, col in (('cc10 yahoo', 'rv_cc10'), ('parkinson10', 'rv_pk10'),
                  ('yangzhang10', 'rv_yz10')):
    rvw = np.clip(df[col].to_numpy(float), 0.05, 2.0)
    Pp, Pq, Pr = nd2_legs(rvw, rvw)
    g = kelly_G(Pp, Pq, Pr)
    dk = dkl3(Pp, Pq, Pr, Qp, Qq, Qr)
    sc = f'SC_{col}'
    df[sc] = g / np.exp(K_CANON * dk)
    arms[f'G_rv {name}'] = sc


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
    print(f'{label:28s} n={s["n"]:5d} avg=${s["avg"]:7.2f} total=${s["tot"]:9.0f} '
          f'WR={s["wr"]:4.1f}% Sh(wk)={s["sh"]:5.2f}{extra}')


def fit_thr(d, col, n_target):
    lo, hi = 0.0, 1.0
    for _ in range(50):
        m = 0.5 * (lo + hi)
        if len(select(d, col, m)) > n_target: lo = m
        else: hi = m
    return 0.5 * (lo + hi)


train = df[df['entry_date'] < SPLIT]
test = df[df['entry_date'] >= SPLIT]
n_tr = len(select(train, 'GAMMA0', THR_CANON))
nF = len(select(df, 'GAMMA0', THR_CANON))

print('\n=== TRAIN | TEST | FULL count-matched ===')
show('canon train', stats(select(train, 'GAMMA0', THR_CANON)))
show('canon test', stats(select(test, 'GAMMA0', THR_CANON)))
show('canon full', stats(select(df, 'GAMMA0', THR_CANON)))
print()
for name, col in arms.items():
    thr = fit_thr(train, col, n_tr)
    show(f'{name} train', stats(select(train, col, thr)))
    show(f'{name} test', stats(select(test, col, thr)))
    thrF = fit_thr(df, col, nF)
    show(f'{name} full', stats(select(df, col, thrF)))
    print()
