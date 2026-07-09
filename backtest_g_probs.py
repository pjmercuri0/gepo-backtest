"""Different values to calculate G: swap the probabilities inside the Kelly
quadratic (payoffs {+b, +ab, -1}, a=(b-1)/2b, ground.py:222-264) while DKL
and k stay canon.

  G_delta (canon) - cached delta-derived p,q,ro  [replication sanity check]
  G_iv            - p,q,ro = nd2 at per-leg IVs (coherent with Q side)
  G_rv            - p,q,ro = nd2 at rv_30d clamp (bet your beliefs)
  G_blend         - normalized geometric mean of the two (robust posterior)

Score = G_x / exp(10 * DKL_cached). Count-matched thresholds on train
2020-2024, test 2025, full-period count-matched. 0.80 x raw_last. Read-only.
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
                       'net_credit', 'max_loss']).copy()
df = df[(df['rv_30d'] > 0) & (df['IV'] > 0) & (df['long_IV'] > 0)
        & (df['max_loss'] > 0) & (df['net_credit'] > 0)]
df['entry_date'] = pd.to_datetime(df['entry_date'])
print(f'{len(df)} candidates')

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
    """Vectorized replica of ground.py Kelly quadratic + ell(w*)."""
    A = -a_par * b * b
    B = a_par * b * b * (p + ro) - b * (p + ro * a_par + q * (1 + a_par))
    C = p * b + ro * a_par * b - q
    w_lin = np.where(b > 0, (p * b - q) / np.maximum(b, 1e-12), np.nan)
    disc = B * B - 4 * A * C
    with np.errstate(invalid='ignore', divide='ignore'):
        s = np.sqrt(np.maximum(disc, 0.0))
        r1 = (-B - s) / (2 * A)
        r2 = (-B + s) / (2 * A)
    in1 = (r1 > 0) & (r1 < 1)
    in2 = (r2 > 0) & (r2 < 1)
    w = np.where(in1, r1, np.where(in2, r2, np.nan))
    w = np.where(A == 0, w_lin, w)
    w = np.where(disc < 0, np.nan, w)
    w = np.clip(w, 0.01, 0.99)

    def lg(x):
        return np.log(np.maximum(x, 1e-10))

    G = (p * lg(1.0 + w * b) + ro * lg(1.0 + w * a_par * b) + q * lg(1.0 - w))
    G = np.where(np.isnan(w), np.nan, G)
    return G


# sanity: replicate cached G from cached probs
gp = df['p'].to_numpy(float)
gq = df['q'].to_numpy(float)
gr = df['ro'].to_numpy(float)
G_chk = kelly_G(gp, gq, gr)
d = np.abs(G_chk - df['G'].to_numpy())
ok = ~np.isnan(d)
print(f'G replication: med {np.nanmedian(d):.5f} p95 {np.nanpercentile(d,95):.5f} '
      f'nan {(~ok).mean()*100:.2f}%')

iv_s = df['IV'].to_numpy(float)
iv_l = df['long_IV'].to_numpy(float)
rv = np.clip(df['rv_30d'].to_numpy(float), 0.05, 2.0)
Qp, Qq, Qr = nd2_legs(iv_s, iv_l)
Pp, Pq, Pr = nd2_legs(rv, rv)
bp = np.sqrt(Pp * Qp); bq = np.sqrt(Pq * Qq); br = np.sqrt(Pr * Qr)
bs_ = bp + bq + br
variants = {
    'G_iv': kelly_G(Qp, Qq, Qr),
    'G_rv': kelly_G(Pp, Pq, Pr),
    'G_blend': kelly_G(bp / bs_, bq / bs_, br / bs_),
}
for name, v in variants.items():
    df[name] = v
    m = ~np.isnan(v)
    print(f'{name}: valid {m.mean()*100:.1f}%  corr_cachedG '
          f'{pd.Series(v).corr(df.G):.3f}  med {np.nanmedian(v):.4f} '
          f'(cached med {df.G.median():.4f})')


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
for name in variants:
    df[f'GM_{name}'] = df[name] / np.exp(K_CANON * df['DKL'])


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
show('canon G_delta k=10 thr=0.075', stats(selA_tr))
fitted = {}
for name in variants:
    col = f'GM_{name}'
    thr = fit_thr(train, col, n_tr)
    show(f'{name:8s} thr={thr:.4f}', stats(select(train, col, thr)))
    fitted[name] = thr

test = df[df['entry_date'] >= SPLIT]
print('\n=== TEST 2025 (train-fitted thresholds) ===')
selA_te = select(test, 'GAMMA0', THR_CANON)
show('canon G_delta k=10 thr=0.075', stats(selA_te))
keysA = set(zip(selA_te['entry_date'], selA_te['ticker'],
                selA_te['short_strike'], selA_te['spread_type']))
for name, thr in fitted.items():
    sel = select(test, f'GM_{name}', thr)
    ks = set(zip(sel['entry_date'], sel['ticker'], sel['short_strike'], sel['spread_type']))
    show(f'{name:8s} thr={thr:.4f}', stats(sel), f'  ovl {len(keysA & ks)}/{len(keysA)}')

print('\n=== FULL PERIOD 2020-2025 count-matched ===')
selF = select(df, 'GAMMA0', THR_CANON)
nF = len(selF)
show('canon full', stats(selF))
for name in variants:
    col = f'GM_{name}'
    thr = fit_thr(df, col, nF)
    show(f'{name:8s} full', stats(select(df, col, thr)))
