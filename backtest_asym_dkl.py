"""Asymmetric / directional DKL variants. Canon penalizes ANY P_rv vs Q_iv
disagreement; here we make the penalty direction-aware:

  rev      - reverse KL D(Q_iv || P_rv)
  jeff     - Jeffreys (symmetrized) 0.5*(D(P||Q)+D(Q||P))
  dang     - one-sided danger: Bernoulli KL on loss prob (q), counted ONLY
             when q_rv > q_iv (market complacent about the loss state);
             rich-premium disagreement (q_iv > q_rv) unpenalized
  dangwin  - one-sided on win prob (p): penalize only when p_iv > p_rv
             (market more optimistic about the win than RV)

P = nd2(rv_30d clamp), Q = nd2 per-leg IV (canon conventions). k grid
{10,20,40}, count-matched thresholds fit on train 2020-2024, test 2025,
full-period count-matched. Realization 0.80 x raw_last. Read-only.
"""
import math
import numpy as np
import pandas as pd
from math import erf

K_CANON = 10.0
THR_CANON = 0.075
LAST_PCT = 0.80
SPLIT = pd.Timestamp('2025-01-01')

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
df = df.dropna(subset=['p', 'q', 'ro', 'G', 'DKL', 'raw_last', 'expiry_close',
                       'rv_30d', 'entry_price', 'IV', 'long_IV']).copy()
df = df[(df['rv_30d'] > 0) & (df['IV'] > 0) & (df['long_IV'] > 0)]
df['entry_date'] = pd.to_datetime(df['entry_date'])
print(f'{len(df)} candidates')

S = df['entry_price'].to_numpy(float)
KS = df['short_strike'].to_numpy(float)
KL_ = df['long_strike'].to_numpy(float)
T = np.maximum(df['DTE'].to_numpy(float), 1.0) / 365.0
is_put = (df['spread_type'] == 'bull_put').to_numpy()


def ncdf(x):
    return 0.5 * (1.0 + np.vectorize(erf)(x / np.sqrt(2.0)))


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


def bern_kl(a, b):
    eps = 1e-9
    a = np.clip(a, eps, 1 - eps)
    b = np.clip(b, eps, 1 - eps)
    return a * np.log(a / b) + (1 - a) * np.log((1 - a) / (1 - b))


iv_s = df['IV'].to_numpy(float)
iv_l = df['long_IV'].to_numpy(float)
rv = np.clip(df['rv_30d'].to_numpy(float), 0.05, 2.0)
Qp, Qq, Qr = nd2_legs(iv_s, iv_l)
Pp, Pq, Pr = nd2_legs(rv, rv)

fwd = dkl3(Pp, Pq, Pr, Qp, Qq, Qr)
diff = np.abs(fwd - df['DKL'].to_numpy())
print(f'DKL replication: med {np.median(diff):.5f} p95 {np.percentile(diff,95):.5f}')

variants = {
    'rev': dkl3(Qp, Qq, Qr, Pp, Pq, Pr),
    'jeff': 0.5 * (fwd + dkl3(Qp, Qq, Qr, Pp, Pq, Pr)),
    'dang': np.where(Pq > Qq, bern_kl(Pq, Qq), 0.0),
    'dangwin': np.where(Qp > Pp, bern_kl(Pp, Qp), 0.0),
}
for name, v in variants.items():
    df[f'DKL_{name}'] = v
    nz = (v > 1e-9).mean()
    print(f'DKL_{name}: p50={np.median(v):.4f} p90={np.percentile(v,90):.4f} '
          f'nonzero={nz*100:.0f}% corr_cached={np.corrcoef(v, df.DKL)[0,1]:.3f}')


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
    print(f'{label:36s} n={s["n"]:5d} avg=${s["avg"]:7.2f} total=${s["tot"]:9.0f} '
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
    for k in (10, 20, 40):
        col = f'GA_{name}_{k}'
        df[col] = df['G'] / np.exp(k * df[f'DKL_{name}'])
        tr = df[df['entry_date'] < SPLIT]
        thr = fit_thr(tr, col, n_tr)
        show(f'{name:8s} k={k:<3} thr={thr:.4f}', stats(select(tr, col, thr)))
        fitted[(name, k)] = thr

test = df[df['entry_date'] >= SPLIT]
print('\n=== TEST 2025 (train-fitted thresholds) ===')
selA_te = select(test, 'GAMMA0', THR_CANON)
show('canon k=10 thr=0.075', stats(selA_te))
keysA = set(zip(selA_te['entry_date'], selA_te['ticker'],
                selA_te['short_strike'], selA_te['spread_type']))
for (name, k), thr in fitted.items():
    sel = select(test, f'GA_{name}_{k}', thr)
    ks = set(zip(sel['entry_date'], sel['ticker'], sel['short_strike'], sel['spread_type']))
    show(f'{name:8s} k={k:<3} thr={thr:.4f}', stats(sel),
         f'  ovl {len(keysA & ks)}/{len(keysA)}')

print('\n=== FULL PERIOD 2020-2025 count-matched ===')
selF = select(df, 'GAMMA0', THR_CANON)
nF = len(selF)
show('canon full', stats(selF))
for (name, k) in fitted:
    col = f'GA_{name}_{k}'
    thr = fit_thr(df, col, nF)
    show(f'{name:8s} k={k:<3} full', stats(select(df, col, thr)))
