"""VRP-centered P: scale RV by the trailing typical IV/RV ratio before
computing P, so the SYSTEMATIC variance premium registers as zero
disagreement and DKL only flags ABNORMAL gaps. Then higher k = pure risk
aversion (the edge is no longer inside the penalty).

c_t = trailing median of (short-leg IV / rv_30d) across all candidates,
rolling 60 entry-days, shifted one day (past data only; expanding fallback
for early dates). P = nd2 probs at clip(rv * c_t, 0.05, 2.0). Q unchanged
(per-leg IV). Train 2020-2024 count-matched, test 2025. Read-only.
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

# trailing typical IV/RV ratio (pooled across tickers)
df['iv_rv'] = df['IV'] / df['rv_30d']
day_ratio = df.groupby('entry_date')['iv_rv'].median().sort_index()
c_roll = day_ratio.rolling(60, min_periods=20).median().shift(1)
c_exp = day_ratio.expanding(min_periods=5).median().shift(1)
c_t = c_roll.fillna(c_exp)
df['c_t'] = df['entry_date'].map(c_t)
df = df[df['c_t'].notna()].copy()
print('c_t (trailing IV/RV): p10=%.2f med=%.2f p90=%.2f' % tuple(
    df['c_t'].quantile([.1, .5, .9])))

S = df['entry_price'].to_numpy(float)
KS = df['short_strike'].to_numpy(float)
KL_ = df['long_strike'].to_numpy(float)
T = np.maximum(df['DTE'].to_numpy(float), 1.0) / 365.0
is_put = (df['spread_type'] == 'bull_put').to_numpy()


def ncdf(x):
    return 0.5 * (1.0 + np.vectorize(erf)(x / np.sqrt(2.0)))


def nd2(sig_s, sig_l):
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


Qp, Qq, Qr = nd2(df['IV'].to_numpy(float), df['long_IV'].to_numpy(float))
rv_c = np.clip(df['rv_30d'].to_numpy(float) * df['c_t'].to_numpy(float), 0.05, 2.0)
Pp, Pq, Pr = nd2(rv_c, rv_c)
df['DKL_c'] = dkl3(Pp, Pq, Pr, Qp, Qq, Qr)
print('DKL_centered: p50=%.4f p90=%.4f | cached: p50=%.4f p90=%.4f corr=%.3f' % (
    df.DKL_c.quantile(.5), df.DKL_c.quantile(.9),
    df.DKL.quantile(.5), df.DKL.quantile(.9), df.DKL_c.corr(df.DKL)))


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
print('\n=== TRAIN 2020-2024 ===')
show('canon k=10, thr=0.075', stats(selA_tr))

fitted = []
for k in [10, 20, 40, 80, 160]:
    col = f'GC_{k}'
    df[col] = df['G'] / np.exp(k * df['DKL_c'])
    tr = df[df['entry_date'] < SPLIT]
    lo_t, hi_t = 0.0, 1.0
    for _ in range(50):
        m = 0.5 * (lo_t + hi_t)
        if len(select(tr, col, m)) > n_target: lo_t = m
        else: hi_t = m
    thr = 0.5 * (lo_t + hi_t)
    show(f'VRP-centered k={k:<4} thr={thr:.4f}', stats(select(tr, col, thr)))
    fitted.append((k, thr))

test = df[df['entry_date'] >= SPLIT]
print('\n=== TEST 2025 (train-fitted thresholds) ===')
selA_te = select(test, 'GAMMA0', THR_CANON)
show('canon k=10, thr=0.075', stats(selA_te))
keysA = set(zip(selA_te['entry_date'], selA_te['ticker'],
                selA_te['short_strike'], selA_te['spread_type']))
for k, thr in fitted:
    sel = select(test, f'GC_{k}', thr)
    ks = set(zip(sel['entry_date'], sel['ticker'], sel['short_strike'], sel['spread_type']))
    show(f'VRP-centered k={k:<4} thr={thr:.4f}', stats(sel),
         f'  ovl {len(keysA & ks)}/{len(keysA)}')

print('\n=== FULL PERIOD count-matched, best-train k ===')
best_k = max(fitted, key=lambda kt: stats(select(df[df['entry_date'] < SPLIT],
                                                 f'GC_{kt[0]}', kt[1]))['avg'])[0]
selF = select(df, 'GAMMA0', THR_CANON)
show('canon full', stats(selF))
nF = len(selF)
lo_t, hi_t = 0.0, 1.0
for _ in range(50):
    m = 0.5 * (lo_t + hi_t)
    if len(select(df, f'GC_{best_k}', m)) > nF: lo_t = m
    else: hi_t = m
selG = select(df, f'GC_{best_k}', 0.5 * (lo_t + hi_t))
show(f'VRP-centered k={best_k} full', stats(selG))
ya = selF.groupby(selF['entry_date'].dt.year)['pnl'].mean()
yb = selG.groupby(selG['entry_date'].dt.year)['pnl'].mean()
print(pd.concat([ya, yb], axis=1, keys=['canon', f'centered_k{best_k}']).round(2).to_string())
