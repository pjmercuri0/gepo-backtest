"""Stress the G_rv / G_blend result (backtest_g_probs.py):
  1. k-sweep {5,10,15,20,40} around canon k=10 (train-fit thr, test, full)
  2. LAST_PCT sensitivity {0.70, 0.80, 0.90} at k=10, full-period count-matched
  3. 2022 anatomy: picks G_rv drops vs adds relative to canon, pnl/oc breakdown
Read-only.
"""
import math
import numpy as np
import pandas as pd
from math import erf

K_CANON = 10.0
THR_CANON = 0.075
SPLIT = pd.Timestamp('2025-01-01')

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
df = df.dropna(subset=['p', 'q', 'ro', 'G', 'DKL', 'raw_last', 'expiry_close',
                       'rv_30d', 'entry_price', 'IV', 'long_IV',
                       'net_credit', 'max_loss']).copy()
df = df[(df['rv_30d'] > 0) & (df['IV'] > 0) & (df['long_IV'] > 0)
        & (df['max_loss'] > 0) & (df['net_credit'] > 0)]
df['entry_date'] = pd.to_datetime(df['entry_date'])

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


iv_s = df['IV'].to_numpy(float)
iv_l = df['long_IV'].to_numpy(float)
rv = np.clip(df['rv_30d'].to_numpy(float), 0.05, 2.0)
Qp, Qq, Qr = nd2_legs(iv_s, iv_l)
Pp, Pq, Pr = nd2_legs(rv, rv)
bp = np.sqrt(Pp * Qp); bq = np.sqrt(Pq * Qq); br = np.sqrt(Pr * Qr)
bs_ = bp + bq + br
df['G_rv'] = kelly_G(Pp, Pq, Pr)
df['G_blend'] = kelly_G(bp / bs_, bq / bs_, br / bs_)


def realized_pnl(d, last_pct):
    credit = last_pct * d['raw_last']
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
    lo, hi = 0.0, 1.0
    for _ in range(50):
        m = 0.5 * (lo + hi)
        if len(select(d, col, m)) > n_target: lo = m
        else: hi = m
    return 0.5 * (lo + hi)


# ---------- 1. k-sweep at LAST_PCT 0.80 ----------
df = realized_pnl(df, 0.80)
df['GAMMA0'] = df['G'] / np.exp(K_CANON * df['DKL'])
train = df[df['entry_date'] < SPLIT]
n_tr = len(select(train, 'GAMMA0', THR_CANON))
nF = len(select(df, 'GAMMA0', THR_CANON))

print('=== 1. k-sweep (train-fit thr | test 2025 | full count-matched) ===')
show('canon k=10 train', stats(select(train, 'GAMMA0', THR_CANON)))
show('canon k=10 test', stats(select(df[df['entry_date'] >= SPLIT], 'GAMMA0', THR_CANON)))
show('canon k=10 full', stats(select(df, 'GAMMA0', THR_CANON)))
for name in ('G_rv', 'G_blend'):
    for k in (5, 10, 15, 20, 40):
        col = f'GS_{name}_{k}'
        df[col] = df[name] / np.exp(k * df['DKL'])
        tr = df[df['entry_date'] < SPLIT]
        te = df[df['entry_date'] >= SPLIT]
        thr = fit_thr(tr, col, n_tr)
        show(f'{name} k={k:<3} train', stats(select(tr, col, thr)))
        show(f'{name} k={k:<3} test', stats(select(te, col, thr)))
        thrF = fit_thr(df, col, nF)
        show(f'{name} k={k:<3} full', stats(select(df, col, thrF)))
        print()

# ---------- 2. LAST_PCT sensitivity at k=10, full count-matched ----------
print('=== 2. LAST_PCT sensitivity (k=10, full count-matched) ===')
for lp in (0.70, 0.80, 0.90):
    d2 = realized_pnl(df, lp)
    show(f'canon  LAST_PCT={lp}', stats(select(d2, 'GAMMA0', THR_CANON)))
    for name in ('G_rv', 'G_blend'):
        col = f'GS_{name}_10'
        thrF = fit_thr(d2, col, nF)
        show(f'{name} LAST_PCT={lp}', stats(select(d2, col, thrF)))
    print()

# ---------- 3. 2022 anatomy ----------
print('=== 3. 2022 anatomy: canon vs G_rv k=10 (full count-matched thr) ===')
thrF = fit_thr(df, 'GS_G_rv_10', nF)
selC = select(df, 'GAMMA0', THR_CANON)
selR = select(df, 'GS_G_rv_10', thrF)
key = ['entry_date', 'ticker', 'short_strike', 'spread_type']
c22 = selC[selC['entry_date'].dt.year == 2022]
r22 = selR[selR['entry_date'].dt.year == 2022]
kc = set(map(tuple, c22[key].values))
kr = set(map(tuple, r22[key].values))
dropped = c22[~c22[key].apply(tuple, axis=1).isin(kr)]
added = r22[~r22[key].apply(tuple, axis=1).isin(kc)]
for lbl, g in (('canon-only (dropped by G_rv)', dropped), ('G_rv-only (added)', added)):
    print(f'{lbl}: n={len(g)} avg=${g.pnl.mean():.2f} '
          f'WR={(g.oc=="WIN").mean()*100:.1f}% '
          f'oc={g.oc.value_counts().to_dict()}')
    print(g.groupby('spread_type')['pnl'].agg(['count', 'mean']).round(2).to_string())
print('\nshared 2022 picks:', len(kc & kr))
