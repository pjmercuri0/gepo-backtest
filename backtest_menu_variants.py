"""Remaining menu options, one harness, five variants.

P-side (different values for P):
  cc5   - close-to-close 5d RV (horizon-matched: spreads live 1-4 days)
  ewma  - EWMA RV lambda=0.94 (RiskMetrics), reacts faster than flat window
  yz10  - Yang-Zhang 10d (Parkinson + overnight gaps + open-close)
  drift - canon rv_30d but trailing 20d annualized drift in the lognormal mean

Q-side (different values for Q):
  qdelta - Q = G's own delta-derived (p,q,ro) from the cache; the penalty
           then audits exactly the probabilities the bet is sized on.

All from Yahoo OHLC (data/daily_bars_yahoo). Universe restricted to rows
with coverage in every variant for fair comparison. k in {10, 20}, count-
matched thresholds fit on train 2020-2024, test 2025, full-period count-
matched. Realization 0.80 x raw_last. Read-only.
"""
import glob, math, os
import numpy as np
import pandas as pd
from math import erf

K_CANON = 10.0
THR_CANON = 0.075
LAST_PCT = 0.80
SPLIT = pd.Timestamp('2025-01-01')

# --- per-ticker daily tables from Yahoo OHLC ---
frames = []
for p in sorted(glob.glob('data/daily_bars_yahoo/*.csv')):
    t = os.path.basename(p)[:-4]
    d = pd.read_csv(p, parse_dates=['date']).sort_values('date')
    d = d[(d['high'] > 0) & (d['low'] > 0) & (d['close'] > 0) & (d['open'] > 0)]
    r = np.log(d['close'] / d['close'].shift(1))
    d['rv_cc5'] = r.rolling(5, min_periods=3).std() * np.sqrt(252)
    ew_var = (r ** 2).ewm(alpha=1 - 0.94, min_periods=5).mean()
    d['rv_ewma'] = np.sqrt(ew_var * 252)
    # Yang-Zhang 10d
    o = np.log(d['open'] / d['close'].shift(1))
    c = np.log(d['close'] / d['open'])
    rs = (np.log(d['high'] / d['close']) * np.log(d['high'] / d['open'])
          + np.log(d['low'] / d['close']) * np.log(d['low'] / d['open']))
    n = 10
    k_yz = 0.34 / (1.34 + (n + 1) / (n - 1))
    v_o = o.rolling(n, min_periods=5).var()
    v_c = c.rolling(n, min_periods=5).var()
    v_rs = rs.rolling(n, min_periods=5).mean()
    d['rv_yz10'] = np.sqrt((v_o + k_yz * v_c + (1 - k_yz) * v_rs).clip(lower=0) * 252)
    d['mu20'] = r.rolling(20, min_periods=10).mean() * 252
    d['ticker'] = t
    frames.append(d[['ticker', 'date', 'rv_cc5', 'rv_ewma', 'rv_yz10', 'mu20']])
tab = pd.concat(frames, ignore_index=True)
print(f'OHLC table: {tab.ticker.nunique()} tickers, {len(tab)} rows')

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
df = df.dropna(subset=['p', 'q', 'ro', 'G', 'DKL', 'raw_last', 'expiry_close',
                       'rv_30d', 'entry_price', 'IV', 'long_IV']).copy()
df = df[(df['rv_30d'] > 0) & (df['IV'] > 0) & (df['long_IV'] > 0)]
df['entry_date'] = pd.to_datetime(df['entry_date'])
df = df.merge(tab, left_on=['ticker', 'entry_date'], right_on=['ticker', 'date'],
              how='left')
need = ['rv_cc5', 'rv_ewma', 'rv_yz10', 'mu20']
cov = df[need].notna().all(axis=1).mean()
df = df[df[need].notna().all(axis=1)].copy()
print(f'{len(df)} candidates after coverage filter ({cov*100:.1f}%)')

S = df['entry_price'].to_numpy(float)
KS = df['short_strike'].to_numpy(float)
KL_ = df['long_strike'].to_numpy(float)
T = np.maximum(df['DTE'].to_numpy(float), 1.0) / 365.0
is_put = (df['spread_type'] == 'bull_put').to_numpy()


def ncdf(x):
    return 0.5 * (1.0 + np.vectorize(erf)(x / np.sqrt(2.0)))


def nd2(sig, mu=0.0):
    sT = sig * np.sqrt(T)
    d2s = (np.log(S / KS) + (mu - 0.5 * sig ** 2) * T) / sT
    d2l = (np.log(S / KL_) + (mu - 0.5 * sig ** 2) * T) / sT
    a = np.where(is_put, ncdf(-d2s), ncdf(d2s))
    b = np.where(is_put, ncdf(-d2l), ncdf(d2l))
    b = np.minimum(b, a)
    p = 1.0 - a
    q = b
    ro = np.maximum(0.0, a - b)
    s = p + q + ro
    return p / s, q / s, ro / s


def nd2_legs(sig_s, sig_l):
    d2s = (np.log(S / KS) - 0.5 * sig_s ** 2 * T) / (sig_s * np.sqrt(T))
    d2l = (np.log(S / KL_) - 0.5 * sig_l ** 2 * T) / (sig_l * np.sqrt(T))
    a = np.where(is_put, ncdf(-d2s), ncdf(d2s))
    b = np.where(is_put, ncdf(-d2l), ncdf(d2l))
    b = np.minimum(b, a)
    p = 1.0 - a
    q = b
    ro = np.maximum(0.0, a - b)
    s = p + q + ro
    return p / s, q / s, ro / s


def dkl3(p1, q1, r1, p2, q2, r2):
    out = np.zeros(len(p1))
    for a, b_ in ((p1, p2), (q1, q2), (r1, r2)):
        m = (a > 0) & (b_ > 0)
        out[m] += a[m] * np.log(a[m] / b_[m])
    return np.maximum(0.0, out)


Qp, Qq, Qr = nd2_legs(df['IV'].to_numpy(float), df['long_IV'].to_numpy(float))

variants = {}
for name, col in (('cc5', 'rv_cc5'), ('ewma', 'rv_ewma'), ('yz10', 'rv_yz10')):
    rv = np.clip(df[col].to_numpy(float), 0.05, 2.0)
    Pp, Pq, Pr = nd2(rv)
    variants[name] = dkl3(Pp, Pq, Pr, Qp, Qq, Qr)
    print(f'{name}: rv/rv_30d med={np.median(df[col]/df.rv_30d):.2f}')

# drift-aware: canon rv_30d, trailing drift in the mean
rv0 = np.clip(df['rv_30d'].to_numpy(float), 0.05, 2.0)
mu = np.clip(df['mu20'].to_numpy(float), -2.0, 2.0)
Pp, Pq, Pr = nd2(rv0, mu=mu)
variants['drift'] = dkl3(Pp, Pq, Pr, Qp, Qq, Qr)

# Q = G's own delta-derived probs, P = canon nd2(rv_30d)
Pp0, Pq0, Pr0 = nd2(rv0)
gp = df['p'].to_numpy(float)
gq = df['q'].to_numpy(float)
gr = df['ro'].to_numpy(float)
gs = gp + gq + gr
variants['qdelta'] = dkl3(Pp0, Pq0, Pr0, gp / gs, gq / gs, gr / gs)

for name, v in variants.items():
    df[f'DKL_{name}'] = v
    print(f'DKL_{name}: p50={np.median(v):.4f} p90={np.percentile(v,90):.4f} '
          f'corr_cached={np.corrcoef(v, df.DKL)[0,1]:.3f}')


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
    print(f'{label:34s} n={s["n"]:5d} avg=${s["avg"]:7.2f} total=${s["tot"]:9.0f} '
          f'WR={s["wr"]:4.1f}% Sh(wk)={s["sh"]:5.2f}{extra}')


def fit_thr(d, col, n_target):
    lo_t, hi_t = 0.0, 1.0
    for _ in range(50):
        m = 0.5 * (lo_t + hi_t)
        if len(select(d, col, m)) > n_target: lo_t = m
        else: hi_t = m
    return 0.5 * (lo_t + hi_t)


train = df[df['entry_date'] < SPLIT]
selA_tr = select(train, 'GAMMA0', THR_CANON)
n_tr = len(selA_tr)
print('\n=== TRAIN 2020-2024 ===')
show('canon k=10 thr=0.075', stats(selA_tr))

fitted = {}
for name in variants:
    for k in (10, 20):
        col = f'GV_{name}_{k}'
        df[col] = df['G'] / np.exp(k * df[f'DKL_{name}'])
        tr = df[df['entry_date'] < SPLIT]
        thr = fit_thr(tr, col, n_tr)
        show(f'{name:7s} k={k:<3} thr={thr:.4f}', stats(select(tr, col, thr)))
        fitted[(name, k)] = thr

test = df[df['entry_date'] >= SPLIT]
print('\n=== TEST 2025 (train-fitted thresholds) ===')
selA_te = select(test, 'GAMMA0', THR_CANON)
show('canon k=10 thr=0.075', stats(selA_te))
keysA = set(zip(selA_te['entry_date'], selA_te['ticker'],
                selA_te['short_strike'], selA_te['spread_type']))
for (name, k), thr in fitted.items():
    sel = select(test, f'GV_{name}_{k}', thr)
    ks = set(zip(sel['entry_date'], sel['ticker'], sel['short_strike'], sel['spread_type']))
    show(f'{name:7s} k={k:<3} thr={thr:.4f}', stats(sel),
         f'  ovl {len(keysA & ks)}/{len(keysA)}')

print('\n=== FULL PERIOD 2020-2025 count-matched ===')
selF = select(df, 'GAMMA0', THR_CANON)
nF = len(selF)
show('canon full', stats(selF))
for (name, k) in fitted:
    col = f'GV_{name}_{k}'
    thr = fit_thr(df, col, nF)
    show(f'{name:7s} k={k:<3} full', stats(select(df, col, thr)))
