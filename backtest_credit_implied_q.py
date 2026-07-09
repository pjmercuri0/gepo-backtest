"""Credit-implied Q: DKL(P_rv || Q_credit) instead of DKL(P_rv || Q_iv).

Q is the 3-state distribution implied by the ACTUAL net credit: solve for
the flat vol sigma_ci that makes the BS spread value (r=0, q=0, T=DTE/365)
equal net_credit, then run the exact nd2_probs_for_spread math at sigma_ci.
The same b that feeds G now also feeds the skepticism term — an inflated
credit raises G but blows up DKL against RV. P side unchanged (rv_30d
clamped [0.05, 2.0], canon convention).

Protocol: k grid fit on 2020-2024 (count-matched), validate on 2025
untouched. Realization 0.80 x raw_last in all arms. Read-only.
"""
import math
import numpy as np
import pandas as pd
from scipy.special import erf as _erf  # fallback below if scipy missing

K_CANON = 10.0
THR_CANON = 0.075
LAST_PCT = 0.80
SPLIT = pd.Timestamp('2025-01-01')

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
df = df.dropna(subset=['p', 'q', 'ro', 'G', 'DKL', 'raw_last', 'expiry_close',
                       'rv_30d', 'entry_price']).copy()
df = df[df['rv_30d'] > 0]
df['entry_date'] = pd.to_datetime(df['entry_date'])
print(f'{len(df)} candidates with valid RV, '
      f'{df.entry_date.min().date()} -> {df.entry_date.max().date()}')


def ncdf(x):
    return 0.5 * (1.0 + _erf(x / np.sqrt(2.0)))


S = df['entry_price'].to_numpy(float)
KS = df['short_strike'].to_numpy(float)
KL_ = df['long_strike'].to_numpy(float)
T = np.maximum(df['DTE'].to_numpy(float), 1.0) / 365.0
C = df['net_credit'].to_numpy(float)
W = df['spread_width'].to_numpy(float)
is_put = (df['spread_type'] == 'bull_put').to_numpy()


def bs_leg(sig, K):
    sT = sig * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * sig * sig * T) / sT
    d2 = d1 - sT
    put = K * ncdf(-d2) - S * ncdf(-d1)
    call = S * ncdf(d1) - K * ncdf(d2)
    return np.where(is_put, put, call)


def spread_credit(sig):
    return bs_leg(sig, KS) - bs_leg(sig, KL_)


# Spread value is NOT monotone in vol for bear calls (rises, peaks, decays
# to 0 as both calls -> S). Find the peak by ternary search, then bisect the
# ASCENDING branch. Credit above the peak value = richer than any vol can
# justify -> sigma_ci = sigma_peak (max disagreement).
a = np.full(len(df), 0.01)
b_hi = np.full(len(df), 8.0)
for _ in range(80):
    m1 = a + (b_hi - a) / 3
    m2 = b_hi - (b_hi - a) / 3
    f1, f2 = spread_credit(m1), spread_credit(m2)
    take_right = f1 < f2
    a = np.where(take_right, m1, a)
    b_hi = np.where(take_right, b_hi, m2)
sigma_peak = 0.5 * (a + b_hi)
c_peak = spread_credit(sigma_peak)

lo = np.full(len(df), 0.01)
hi = sigma_peak.copy()
below = C <= spread_credit(lo)   # quote cheaper than near-zero-vol value
above = C >= c_peak              # quote richer than any vol justifies
for _ in range(70):
    mid = 0.5 * (lo + hi)
    cm = spread_credit(mid)
    go_up = cm < C
    lo = np.where(go_up, mid, lo)
    hi = np.where(go_up, hi, mid)
sigma_ci = 0.5 * (lo + hi)
sigma_ci = np.where(below, 0.01, np.where(above, sigma_peak, sigma_ci))
resid = np.abs(spread_credit(sigma_ci) - C)
ok_fit = ~(below | above)
print(f'sigma_ci solved: {ok_fit.mean()*100:.1f}% on ascending branch '
      f'(median |resid| {np.median(resid[ok_fit]):.4f}); '
      f'below-bracket {below.mean()*100:.2f}%, above-peak {above.mean()*100:.2f}%')
print('sigma_ci vs leg IV: ratio p10=%.2f med=%.2f p90=%.2f' % tuple(
    np.percentile(sigma_ci / df['IV'].to_numpy(float), [10, 50, 90])))


def nd2_probs(sig):
    sig = np.asarray(sig, float)
    sT = sig * np.sqrt(T)
    d2s = (np.log(S / KS) - 0.5 * sig * sig * T) / sT
    d2l = (np.log(S / KL_) - 0.5 * sig * sig * T) / sT
    ps_itm = np.where(is_put, ncdf(-d2s), ncdf(d2s))
    pl_itm = np.where(is_put, ncdf(-d2l), ncdf(d2l))
    pl_itm = np.minimum(pl_itm, ps_itm)
    p = 1.0 - ps_itm
    q = pl_itm
    ro = np.maximum(0.0, ps_itm - pl_itm)
    s = p + q + ro
    return p / s, q / s, ro / s


rv = np.clip(df['rv_30d'].to_numpy(float), 0.05, 2.0)
p_rv, q_rv, ro_rv = nd2_probs(rv)
p_ci, q_ci, ro_ci = nd2_probs(sigma_ci)


def dkl3(p1, q1, r1, p2, q2, r2):
    out = np.zeros(len(p1))
    for a, b_ in ((p1, p2), (q1, q2), (r1, r2)):
        m = (a > 0) & (b_ > 0)
        out[m] += a[m] * np.log(a[m] / b_[m])
    return np.maximum(0.0, out)


df['DKL_ci'] = dkl3(p_rv, q_rv, ro_rv, p_ci, q_ci, ro_ci)
print('DKL_ci: p50=%.4f p90=%.4f  | cached DKL_iv: p50=%.4f p90=%.4f  corr=%.3f' % (
    df['DKL_ci'].quantile(.5), df['DKL_ci'].quantile(.9),
    df['DKL'].quantile(.5), df['DKL'].quantile(.9),
    df['DKL_ci'].corr(df['DKL'])))


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
    print(f'{label:40s} n={s["n"]:5d} avg=${s["avg"]:7.2f} total=${s["tot"]:9.0f} '
          f'WR={s["wr"]:4.1f}% Sh(wk)={s["sh"]:5.2f}{extra}')


train = df[df['entry_date'] < SPLIT]
selA_tr = select(train, 'GAMMA0', THR_CANON)
n_target = len(selA_tr)
print(f'\n=== TRAIN 2020-2024 ===')
show('canon Q_iv k=10, thr=0.075', stats(selA_tr))

fitted = []
for k in [2, 5, 10, 15, 20, 40]:
    col = f'GCI_{k}'
    df[col] = df['G'] / np.exp(k * df['DKL_ci'])
    tr = df[df['entry_date'] < SPLIT]
    lo_t, hi_t = 0.0, 1.0
    for _ in range(50):
        m = 0.5 * (lo_t + hi_t)
        if len(select(tr, col, m)) > n_target: lo_t = m
        else: hi_t = m
    thr = 0.5 * (lo_t + hi_t)
    show(f'Q_credit k={k:<3} thr={thr:.4f}', stats(select(tr, col, thr)))
    fitted.append((k, thr))

test = df[df['entry_date'] >= SPLIT]
print(f'\n=== TEST 2025 (train-fitted thresholds) ===')
selA_te = select(test, 'GAMMA0', THR_CANON)
show('canon Q_iv k=10, thr=0.075', stats(selA_te))
keysA = set(zip(selA_te['entry_date'], selA_te['ticker'],
                selA_te['short_strike'], selA_te['spread_type']))
for k, thr in fitted:
    sel = select(test, f'GCI_{k}', thr)
    ks = set(zip(sel['entry_date'], sel['ticker'], sel['short_strike'], sel['spread_type']))
    ov = len(keysA & ks)
    show(f'Q_credit k={k:<3} thr={thr:.4f}', stats(sel),
         f'  ovl {ov}/{len(keysA)}')

cols = ['entry_date', 'ticker', 'short_strike', 'spread_type', 'G', 'DKL', 'DKL_ci',
        'GAMMA0', 'pnl', 'oc']
out = df[cols].copy()
out['sigma_ci'] = sigma_ci
out.to_csv('/tmp/credit_implied_q.csv', index=False)
print('\nwrote /tmp/credit_implied_q.csv')
