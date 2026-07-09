"""Task: does GROUND selection survive scoring on the REALIZED credit basis?

Canon scores b = net_credit/max_loss from FULL clamped LAST, then realizes at
0.80x LAST. That ranks by credit you don't get (winner's-curse toward inflated
quotes). Here we rescore the same 22,702 no-regime candidates with
b' = 0.8c / (w - 0.8c) and alpha from b', and compare selections.

Read-only on caches; writes nothing to output/.
"""
import math
import numpy as np
import pandas as pd

K = 10.0
THR_CANON = 0.075
LAST_PCT = 0.80

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
df = df.dropna(subset=['p', 'q', 'ro', 'G', 'DKL', 'raw_last', 'expiry_close']).copy()
df['entry_date'] = pd.to_datetime(df['entry_date'])
print(f'{len(df)} candidates, {df.entry_date.min().date()} -> {df.entry_date.max().date()}')


def kelly_G(p, q, ro, b):
    if b <= 0:
        return None
    a = 0.0 if b >= 1.0 else (b - 1.0) / (2.0 * b)
    A = -a * b * b
    B = a * b * b * (p + ro) - b * (p + ro * a + q * (1 + a))
    C = p * b + ro * a * b - q
    if A == 0:
        w = (p * b - q) / b
    else:
        disc = B * B - 4 * A * C
        if disc < 0:
            return None
        s = math.sqrt(disc)
        cands = [r for r in ((-B - s) / (2 * A), (-B + s) / (2 * A)) if 0 < r < 1]
        if not cands:
            return None
        w = cands[0]
    w = float(np.clip(w, 0.01, 0.99))
    lg = lambda x: math.log(max(x, 1e-10))
    return (p * lg(1 + w * b) + ro * lg(1 + w * a * b) + q * lg(1 - w))


def add_scores(d):
    # sanity: replicate cached G from full-credit b
    d['b_full'] = d['net_credit'] / d['max_loss']
    d['G_chk'] = [kelly_G(p, q, ro, b) for p, q, ro, b in
                  zip(d['p'], d['q'], d['ro'], d['b_full'])]
    # haircut basis
    d['w_tot'] = d['net_credit'] + d['max_loss']
    d['c_hc'] = LAST_PCT * d['net_credit']
    d['ml_hc'] = d['w_tot'] - d['c_hc']
    d['b_hc'] = d['c_hc'] / d['ml_hc']
    d['G_hc'] = [kelly_G(p, q, ro, b) for p, q, ro, b in
                 zip(d['p'], d['q'], d['ro'], d['b_hc'])]
    d['GAMMA'] = d['G'] / np.exp(K * d['DKL'])
    d['GAMMA_chk'] = d['G_chk'] / np.exp(K * d['DKL'])
    d['GAMMA_hc'] = d['G_hc'] / np.exp(K * d['DKL'])
    return d


df = add_scores(df)
chk = (df['G_chk'] - df['G']).abs()
print(f'G replication check: max abs diff {chk.max():.6f} median {chk.median():.6f}')


def realized_pnl(d):
    credit = LAST_PCT * d['raw_last']
    width = d['width']
    ml_adj = width - credit
    out = []
    for cr, w, ss, ls, sp, st in zip(credit, width, d['short_strike'],
                                     d['long_strike'], d['expiry_close'], d['spread_type']):
        if st == 'bull_put':
            intr = min(max(ss - sp, 0.0), w)
            oc = 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
        else:
            intr = min(max(sp - ss, 0.0), w)
            oc = 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')
        pnl = (cr - intr) * 100
        if oc == 'PARTIAL' and pnl > 0:
            pnl *= 0.5
        out.append((pnl, oc, ml_adj * 100))
    d = d.copy()
    d[['pnl', 'oc', 'ml_dollar']] = pd.DataFrame(out, index=d.index)
    return d


df = realized_pnl(df)


def select(d, score_col, thr):
    q = d[d[score_col] >= thr]
    return (q.sort_values(['entry_date', score_col], ascending=[True, False])
             .groupby('entry_date').head(5))


def metrics(sel, label):
    if len(sel) == 0:
        print(f'{label}: 0 picks'); return
    daily = sel.groupby(sel['realize_date'])['pnl'].sum()
    eq = 10000 + daily.sort_index().cumsum()
    wk = eq.resample('W').last().ffill()
    r = wk.pct_change().dropna()
    sh = r.mean() / r.std() * math.sqrt(52) if r.std() > 0 else float('nan')
    wr = (sel['oc'] == 'WIN').mean() * 100
    print(f'{label:34s} n={len(sel):5d} avg=${sel.pnl.mean():7.2f} total=${sel.pnl.sum():9.0f} '
          f'WR={wr:4.1f}% Sh(wk)={sh:5.2f} final=${eq.iloc[-1]:,.0f}')
    return set(zip(sel['entry_date'], sel['ticker'], sel['short_strike'], sel['spread_type']))


print('\n=== A. CANON: select on full-LAST GROUND, realize at 0.80 ===')
selA = select(df, 'GAMMA', THR_CANON)
keysA = metrics(selA, f'full-LAST b, thr={THR_CANON}')

print('\n=== B. HAIRCUT BASIS: select on 0.80-LAST GROUND ===')
for thr in [0.075, 0.050, 0.040, 0.030, 0.020, 0.010, 0.005]:
    metrics(select(df, 'GAMMA_hc', thr), f'haircut b, thr={thr}')

# count-matched comparison
nA = len(selA)
lo, hi = 0.0, 0.2
for _ in range(40):
    mid = (lo + hi) / 2
    n = len(select(df, 'GAMMA_hc', mid))
    if n > nA: lo = mid
    else: hi = mid
thr_m = (lo + hi) / 2
selB = select(df, 'GAMMA_hc', thr_m)
print(f'\n=== count-matched: haircut thr={thr_m:.4f} ===')
keysB = metrics(selB, f'haircut b, thr={thr_m:.4f}')
if keysA and keysB:
    ov = len(keysA & keysB)
    print(f'overlap with canon selection: {ov}/{nA} ({ov/nA*100:.1f}%)')
