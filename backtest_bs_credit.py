"""Selection on PURELY THEORETICAL (BS) credit vs canon LAST-credit.

Per candidate: theo net_credit = BS(short leg, per-leg IV) - BS(long leg),
r=0 q=0 (live/bs_pricing.py conventions). b_theo = c/(w-c), G recomputed
via the Kelly quadratic, GAMMA_theo = G_theo*exp(-10*DKL). Realization is
UNCHANGED (0.80 x raw_last, partial 50% haircut) so only selection moves.
Read-only.
"""
import math
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, '.')
from live.bs_pricing import bs_spread_debit

K = 10.0
THR_CANON = 0.075
LAST_PCT = 0.80

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
df = df.dropna(subset=['p', 'q', 'ro', 'G', 'DKL', 'raw_last', 'expiry_close']).copy()
df['entry_date'] = pd.to_datetime(df['entry_date'])
print(f'{len(df)} candidates, {df.entry_date.min().date()} -> {df.entry_date.max().date()}')

df['c_theo'] = [bs_spread_debit(sp, ss, ls, iv_s, iv_l, dte, st)
                for sp, ss, ls, iv_s, iv_l, dte, st in
                zip(df['entry_price'], df['short_strike'], df['long_strike'],
                    df['IV'], df['long_IV'], df['DTE'], df['spread_type'])]
df['c_theo'] = df['c_theo'].clip(upper=0.99 * df['spread_width'])

ratio = df['c_theo'] / df['net_credit']
print(f'theo/LAST credit ratio: p10={ratio.quantile(.1):.2f} med={ratio.median():.2f} '
      f'p90={ratio.quantile(.9):.2f}  (c_theo==0: {(df.c_theo<=0).mean()*100:.1f}%)')


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
    return p * lg(1 + w * b) + ro * lg(1 + w * a * b) + q * lg(1 - w)


df['b_theo'] = df['c_theo'] / (df['spread_width'] - df['c_theo'])
df['G_theo'] = [kelly_G(p, q, ro, b) for p, q, ro, b in
                zip(df['p'], df['q'], df['ro'], df['b_theo'])]
df['GAMMA'] = df['G'] / np.exp(K * df['DKL'])
df['GAMMA_theo'] = df['G_theo'] / np.exp(K * df['DKL'])
print(f'G_theo computable for {df.G_theo.notna().mean()*100:.1f}% of candidates')


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
    q = d[d[score_col].notna() & (d[score_col] >= thr)]
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
    print(f'{label:38s} n={len(sel):5d} avg=${sel.pnl.mean():7.2f} total=${sel.pnl.sum():9.0f} '
          f'WR={wr:4.1f}% Sh(wk)={sh:5.2f} final=${eq.iloc[-1]:,.0f}')
    return set(zip(sel['entry_date'], sel['ticker'], sel['short_strike'], sel['spread_type']))


print('\n=== A. CANON: LAST-credit GROUND, thr=0.075 ===')
selA = select(df, 'GAMMA', THR_CANON)
keysA = metrics(selA, 'canon (LAST b)')
nA = len(selA)

print('\n=== B. THEORETICAL-credit GROUND, threshold sweep ===')
for thr in [0.075, 0.050, 0.030, 0.020, 0.010, 0.005]:
    metrics(select(df, 'GAMMA_theo', thr), f'theo b, thr={thr}')

lo, hi = 0.0, 0.5
for _ in range(50):
    mid = 0.5 * (lo + hi)
    if len(select(df, 'GAMMA_theo', mid)) > nA: lo = mid
    else: hi = mid
thr_m = 0.5 * (lo + hi)
print(f'\n=== count-matched: theo thr={thr_m:.5f} ===')
selB = select(df, 'GAMMA_theo', thr_m)
keysB = metrics(selB, f'theo b, thr={thr_m:.5f}')
if keysA and keysB:
    ov = len(keysA & keysB)
    print(f'overlap with canon: {ov}/{nA} ({ov/nA*100:.1f}%)')

per_yr = selB.groupby(selB['entry_date'].dt.year)['pnl'].agg(['count', 'mean', 'sum'])
print('\ntheo count-matched by year:')
print(per_yr.to_string())

df[['entry_date', 'ticker', 'short_strike', 'spread_type', 'net_credit', 'c_theo',
    'G', 'G_theo', 'DKL', 'GAMMA', 'GAMMA_theo', 'pnl', 'oc']].to_csv(
    '/tmp/bs_credit_scores.csv', index=False)
print('wrote /tmp/bs_credit_scores.csv')
