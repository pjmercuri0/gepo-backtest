"""Route 1: robust-Kelly (KL-ball) ranking vs canon GROUND.

Score each candidate by worst-case expected log-growth over all measures P
within DKL(P||Q) <= eta of the delta-derived belief Q=(p,ro,q), with
eta = measured DKL(P_rv||Q_iv) capped at the simplex bound ln(1/q).

Exact inner solution: exponential tilt P*(i) ~ Q(i)exp(-l_i/theta), theta
solved by bisection so DKL(P*||Q)=eta. Robust score = E_{P*}[l] at the
nominal Kelly w*. Also reports the first-order form G - sqrt(2*eta)*sigma_Q(l)
and the implied effective k. Read-only.
"""
import math
import numpy as np
import pandas as pd

K = 10.0
THR_CANON = 0.075
LAST_PCT = 0.80

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
df = df.dropna(subset=['p', 'q', 'ro', 'G', 'DKL', 'w_star', 'raw_last', 'expiry_close']).copy()
df['entry_date'] = pd.to_datetime(df['entry_date'])
print(f'{len(df)} candidates, {df.entry_date.min().date()} -> {df.entry_date.max().date()}')

# 3-state log-outcomes at nominal w*
b = (df['net_credit'] / df['max_loss']).to_numpy()
a = np.where(b >= 1.0, 0.0, (b - 1.0) / (2.0 * b))
w = df['w_star'].to_numpy()
L = np.stack([np.log(np.maximum(1 + w * b, 1e-10)),
              np.log(np.maximum(1 + w * a * b, 1e-10)),
              np.log(np.maximum(1 - w, 1e-10))], axis=1)
Q = df[['p', 'ro', 'q']].to_numpy()
logQ = np.log(np.maximum(Q, 1e-12))

G_chk = (Q * L).sum(axis=1)
print(f'G replication: max abs diff {np.abs(G_chk - df.G.to_numpy()).max():.6f}')

# eta = measured DKL, capped just below the simplex bound ln(1/q)
eta = np.minimum(df['DKL'].to_numpy(), 0.999 * np.log(1.0 / np.maximum(Q[:, 2], 1e-12)))
n_capped = int((df['DKL'].to_numpy() > eta + 1e-12).sum())
print(f'eta capped at ln(1/q) for {n_capped} candidates')


def tilt_dkl(log_theta):
    th = np.exp(log_theta)
    logits = logQ - L / th[:, None]
    logits -= logits.max(axis=1, keepdims=True)
    P = np.exp(logits)
    P /= P.sum(axis=1, keepdims=True)
    dkl = (P * (np.log(np.maximum(P, 1e-300)) - logQ)).sum(axis=1)
    return P, dkl


# DKL(theta) decreases in theta: bisect log-theta in [-20, 20]
lo = np.full(len(df), -20.0)
hi = np.full(len(df), 20.0)
for _ in range(80):
    mid = 0.5 * (lo + hi)
    _, dkl = tilt_dkl(mid)
    too_small = dkl < eta      # need smaller theta
    hi = np.where(too_small, mid, hi)
    lo = np.where(too_small, lo, mid)
Pstar, dkl_fit = tilt_dkl(0.5 * (lo + hi))
print(f'theta fit: max |DKL(P*)-eta| = {np.abs(dkl_fit - eta).max():.2e}')

df['G_rob'] = (Pstar * L).sum(axis=1)

# first-order form and implied effective k
var_l = (Q * L * L).sum(axis=1) - G_chk ** 2
df['G_fo'] = G_chk - np.sqrt(2.0 * eta * var_l)
pen = G_chk - df['G_rob'].to_numpy()
with np.errstate(divide='ignore', invalid='ignore'):
    k_eff = np.where(eta > 1e-6, pen / eta, np.nan)
print(f'\nimplied k_eff = (G - G_rob)/eta:  '
      f'p10={np.nanpercentile(k_eff,10):.1f} med={np.nanmedian(k_eff):.1f} '
      f'p90={np.nanpercentile(k_eff,90):.1f}')

df['GAMMA'] = df['G'] / np.exp(K * df['DKL'])


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


def select(d, score_col, thr):
    q = d[d[score_col] >= thr]
    return (q.sort_values(['entry_date', score_col], ascending=[True, False])
             .groupby('entry_date').head(5))


def metrics(sel, label):
    if len(sel) == 0:
        print(f'{label}: 0 picks'); return None
    daily = sel.groupby(sel['realize_date'])['pnl'].sum()
    eq = 10000 + daily.sort_index().cumsum()
    wk = eq.resample('W').last().ffill()
    r = wk.pct_change().dropna()
    sh = r.mean() / r.std() * math.sqrt(52) if r.std() > 0 else float('nan')
    wr = (sel['oc'] == 'WIN').mean() * 100
    print(f'{label:40s} n={len(sel):5d} avg=${sel.pnl.mean():7.2f} total=${sel.pnl.sum():9.0f} '
          f'WR={wr:4.1f}% Sh(wk)={sh:5.2f} final=${eq.iloc[-1]:,.0f}')
    return set(zip(sel['entry_date'], sel['ticker'], sel['short_strike'], sel['spread_type']))


def count_matched(d, score_col, n_target, vlo, vhi):
    for _ in range(50):
        mid = 0.5 * (vlo + vhi)
        n = len(select(d, score_col, mid))
        if n > n_target: vlo = mid
        else: vhi = mid
    return 0.5 * (vlo + vhi)


print('\n=== A. CANON GROUND, thr=0.075 ===')
selA = select(df, 'GAMMA', THR_CANON)
keysA = metrics(selA, 'canon GAMMA')
nA = len(selA)

print('\n=== B. ROBUST KELLY (exact tilt), count-matched ===')
thrB = count_matched(df, 'G_rob', nA, df['G_rob'].min(), df['G_rob'].max())
selB = select(df, 'G_rob', thrB)
keysB = metrics(selB, f'robust G_rob, thr={thrB:.5f}')

print('\n=== C. FIRST-ORDER sqrt form, count-matched ===')
thrC = count_matched(df, 'G_fo', nA, df['G_fo'].min(), df['G_fo'].max())
selC = select(df, 'G_fo', thrC)
keysC = metrics(selC, f'first-order G_fo, thr={thrC:.5f}')

for nm, ks in [('robust', keysB), ('first-order', keysC)]:
    if keysA and ks:
        ov = len(keysA & ks)
        print(f'overlap {nm} vs canon: {ov}/{nA} ({ov/nA*100:.1f}%)')

df[['entry_date', 'ticker', 'short_strike', 'spread_type', 'G', 'DKL',
    'GAMMA', 'G_rob', 'G_fo', 'pnl', 'oc']].to_csv('/tmp/robust_kelly_scores.csv', index=False)
print('wrote /tmp/robust_kelly_scores.csv')
