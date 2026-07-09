"""Asymmetric BS-anchored penalty as a SECOND channel on top of canon GAMMA.

Solve credit-implied vol sigma_ci (BS spread value = net credit; ternary
peak + ascending-branch bisection, as validated in backtest_credit_implied_q).
Let ratio = sigma_ci / IV_short. Band logic:

  cheap   - sigma_ci < IV: quote pays less than its own IV says it should;
            penalty = D(Q_ci || Q_iv) (3-state), only on this side
  rich    - ratio above the TRAILING cross-sectional p90 (60 entry-days,
            shifted 1 day, expanding fallback): suspicious/stale print;
            penalty = D(Q_ci || Q_iv) only beyond the band
  normal richness band in between: zero penalty (that's the edge)

Score = GAMMA0 * exp(-k2 * (DKL_cheap [+ DKL_rich])).
Arms: cheap-only, rich-only, both; k2 grid {2,5,10,20}. Count-matched
thresholds on train 2020-2024, test 2025, full-period count-matched.
Realization 0.80 x raw_last. Read-only.
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
                       'rv_30d', 'entry_price', 'IV', 'long_IV',
                       'net_credit', 'spread_width']).copy()
df = df[(df['rv_30d'] > 0) & (df['IV'] > 0) & (df['long_IV'] > 0)]
df['entry_date'] = pd.to_datetime(df['entry_date'])
print(f'{len(df)} candidates')

S = df['entry_price'].to_numpy(float)
KS = df['short_strike'].to_numpy(float)
KL_ = df['long_strike'].to_numpy(float)
T = np.maximum(df['DTE'].to_numpy(float), 1.0) / 365.0
C = df['net_credit'].to_numpy(float)
is_put = (df['spread_type'] == 'bull_put').to_numpy()


def ncdf(x):
    return 0.5 * (1.0 + np.vectorize(erf)(x / np.sqrt(2.0)))


def bs_leg(sig, K):
    sT = sig * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * sig * sig * T) / sT
    d2 = d1 - sT
    put = K * ncdf(-d2) - S * ncdf(-d1)
    call = S * ncdf(d1) - K * ncdf(d2)
    return np.where(is_put, put, call)


def spread_credit(sig):
    return bs_leg(sig, KS) - bs_leg(sig, KL_)


# sigma_ci: ternary-search peak, bisect ascending branch (bear calls non-monotone)
a = np.full(len(df), 0.01)
b_hi = np.full(len(df), 8.0)
for _ in range(80):
    m1 = a + (b_hi - a) / 3
    m2 = b_hi - (b_hi - a) / 3
    take_right = spread_credit(m1) < spread_credit(m2)
    a = np.where(take_right, m1, a)
    b_hi = np.where(take_right, b_hi, m2)
sigma_peak = 0.5 * (a + b_hi)
c_peak = spread_credit(sigma_peak)

lo = np.full(len(df), 0.01)
hi = sigma_peak.copy()
below = C <= spread_credit(lo)
above = C >= c_peak
for _ in range(70):
    mid = 0.5 * (lo + hi)
    go_up = spread_credit(mid) < C
    lo = np.where(go_up, mid, lo)
    hi = np.where(go_up, hi, mid)
sigma_ci = 0.5 * (lo + hi)
sigma_ci = np.where(below, 0.01, np.where(above, sigma_peak, sigma_ci))
iv_s = df['IV'].to_numpy(float)
ratio = sigma_ci / iv_s
df['ratio_ci'] = ratio
print('sigma_ci/IV: p10=%.2f med=%.2f p90=%.2f; above-peak %.2f%%, below %.2f%%' % (
    *np.percentile(ratio, [10, 50, 90]), above.mean() * 100, below.mean() * 100))

# trailing cross-sectional p90 of ratio (past data only)
day_p90 = df.groupby('entry_date')['ratio_ci'].quantile(0.9).sort_index()
band_roll = day_p90.rolling(60, min_periods=20).median().shift(1)
band_exp = day_p90.expanding(min_periods=5).median().shift(1)
band = band_roll.fillna(band_exp)
df['rich_cap'] = df['entry_date'].map(band)
df = df[df['rich_cap'].notna()].copy()
keep = df.index.to_numpy()
# re-align arrays after the band filter
pos = df.reset_index().index.to_numpy()
mask_keep = np.isin(np.arange(len(ratio)), [])  # placeholder, realign below
arr_keep = df['ratio_ci'].to_numpy()
print('rich cap (trailing p90 of ratio): med=%.2f' % df['rich_cap'].median())


def nd2_at(sig_s, sig_l, S_, KS_, KL2, T_, isp):
    d2s = (np.log(S_ / KS_) - 0.5 * sig_s ** 2 * T_) / (sig_s * np.sqrt(T_))
    d2l = (np.log(S_ / KL2) - 0.5 * sig_l ** 2 * T_) / (sig_l * np.sqrt(T_))
    a_ = np.where(isp, ncdf(-d2s), ncdf(d2s))
    b_ = np.where(isp, ncdf(-d2l), ncdf(d2l))
    b_ = np.minimum(b_, a_)
    p = 1.0 - a_
    q = b_
    ro = np.maximum(0.0, a_ - b_)
    s = p + q + ro
    return p / s, q / s, ro / s


def dkl3(p1, q1, r1, p2, q2, r2):
    out = np.zeros(len(p1))
    for x, y in ((p1, p2), (q1, q2), (r1, r2)):
        m = (x > 0) & (y > 0)
        out[m] += x[m] * np.log(x[m] / y[m])
    return np.maximum(0.0, out)


# recompute on the filtered frame
S = df['entry_price'].to_numpy(float)
KS = df['short_strike'].to_numpy(float)
KL_ = df['long_strike'].to_numpy(float)
T = np.maximum(df['DTE'].to_numpy(float), 1.0) / 365.0
is_put = (df['spread_type'] == 'bull_put').to_numpy()
iv_s = df['IV'].to_numpy(float)
iv_l = df['long_IV'].to_numpy(float)
sci = df['ratio_ci'].to_numpy() * iv_s
cap = df['rich_cap'].to_numpy(float)
ratio = df['ratio_ci'].to_numpy(float)

Qci = nd2_at(sci, sci, S, KS, KL_, T, is_put)
Qiv = nd2_at(iv_s, iv_l, S, KS, KL_, T, is_put)
d_full = dkl3(*Qci, *Qiv)

df['DKL_cheap'] = np.where(ratio < 1.0, d_full, 0.0)
df['DKL_rich'] = np.where(ratio > cap, d_full, 0.0)
df['DKL_both'] = df['DKL_cheap'] + df['DKL_rich']
for c in ('DKL_cheap', 'DKL_rich', 'DKL_both'):
    v = df[c].to_numpy()
    print(f'{c}: nonzero={(v>1e-9).mean()*100:.0f}% p90={np.percentile(v,90):.4f}')


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
for name in ('cheap', 'rich', 'both'):
    for k2 in (2, 5, 10, 20):
        col = f'GB_{name}_{k2}'
        df[col] = df['GAMMA0'] * np.exp(-k2 * df[f'DKL_{name}'])
        tr = df[df['entry_date'] < SPLIT]
        thr = fit_thr(tr, col, n_tr)
        show(f'{name:6s} k2={k2:<3} thr={thr:.4f}', stats(select(tr, col, thr)))
        fitted[(name, k2)] = thr

test = df[df['entry_date'] >= SPLIT]
print('\n=== TEST 2025 (train-fitted thresholds) ===')
selA_te = select(test, 'GAMMA0', THR_CANON)
show('canon k=10 thr=0.075', stats(selA_te))
keysA = set(zip(selA_te['entry_date'], selA_te['ticker'],
                selA_te['short_strike'], selA_te['spread_type']))
for (name, k2), thr in fitted.items():
    sel = select(test, f'GB_{name}_{k2}', thr)
    ks = set(zip(sel['entry_date'], sel['ticker'], sel['short_strike'], sel['spread_type']))
    show(f'{name:6s} k2={k2:<3} thr={thr:.4f}', stats(sel),
         f'  ovl {len(keysA & ks)}/{len(keysA)}')

print('\n=== FULL PERIOD 2020-2025 count-matched ===')
selF = select(df, 'GAMMA0', THR_CANON)
nF = len(selF)
show('canon full', stats(selF))
for (name, k2) in fitted:
    col = f'GB_{name}_{k2}'
    thr = fit_thr(df, col, nF)
    show(f'{name:6s} k2={k2:<3} full', stats(select(df, col, thr)))
