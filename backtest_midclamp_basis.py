"""Validate the anti-inflation clamp (short: min(LAST,MID); long: max(LAST,MID)).

Recomputes net_credit from cached per-leg quotes in the NOREGIME parquet,
rescores G/GROUND on the clamped basis, and compares selection vs canon.
Realization identical for both arms (0.80 x old raw_last). Read-only.
"""
import math
import numpy as np
import pandas as pd

K = 10.0
THR_CANON = 0.075
LAST_PCT = 0.80
MIN_CR = 0.30

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
df = df.dropna(subset=['p', 'q', 'ro', 'G', 'DKL', 'raw_last', 'expiry_close',
                       'short_bid', 'short_ask', 'long_bid', 'long_ask',
                       'short_last', 'long_last']).copy()
df['entry_date'] = pd.to_datetime(df['entry_date'])
print(f'{len(df)} candidates, {df.entry_date.min().date()} -> {df.entry_date.max().date()}')

sm = (df['short_bid'] + df['short_ask']) / 2
lm = (df['long_bid'] + df['long_ask']) / 2
sc = np.maximum(df['short_bid'], np.minimum(df['short_last'], df['short_ask']))
lc = np.maximum(df['long_bid'], np.minimum(df['long_last'], df['long_ask']))
sc2 = np.minimum(sc, sm)
lc2 = np.maximum(lc, lm)
df['nc_new'] = (sc2 - lc2).round(4)

old_nc = df['net_credit']
changed = (df['nc_new'] - old_nc).abs() > 1e-9
print(f'credit changed for {changed.mean()*100:.1f}% of candidates; '
      f'median change among changed: {(df.nc_new-old_nc)[changed].median():+.3f} '
      f'(new/old med {(df.nc_new/old_nc)[changed].median():.2f})')

df['ml_new'] = df['spread_width'] - df['nc_new']
df['cr_new'] = df['nc_new'] / df['ml_new']
alive = (df['nc_new'] > 0) & (df['ml_new'] > 0) & (df['cr_new'] >= MIN_CR)
print(f'survive credit-ratio filters under new basis: {alive.mean()*100:.1f}%')


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


df['G_new'] = [kelly_G(p, q, ro, b) if ok else None for p, q, ro, b, ok in
               zip(df['p'], df['q'], df['ro'], df['cr_new'], alive)]
df['GAMMA'] = df['G'] / np.exp(K * df['DKL'])
df['GAMMA_new'] = df['G_new'] / np.exp(K * df['DKL'])


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


def select(d, col, thr):
    q = d[d[col].notna() & (d[col] >= thr)]
    return (q.sort_values(['entry_date', col], ascending=[True, False])
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


print('\n=== A. CANON (old basis), thr=0.075 ===')
selA = select(df, 'GAMMA', THR_CANON)
keysA = metrics(selA, 'canon old basis')
nA = len(selA)

print('\n=== B. ANTI-INFLATION CLAMP basis ===')
selB75 = select(df, 'GAMMA_new', THR_CANON)
keysB75 = metrics(selB75, 'clamped basis, thr=0.075')

lo, hi = 0.0, 1.0
for _ in range(50):
    mid = 0.5 * (lo + hi)
    if len(select(df, 'GAMMA_new', mid)) > nA: lo = mid
    else: hi = mid
thr_m = 0.5 * (lo + hi)
selB = select(df, 'GAMMA_new', thr_m)
keysB = metrics(selB, f'clamped basis, count-matched thr={thr_m:.4f}')

for nm, ks, n in [('thr=0.075', keysB75, len(selB75)), ('count-matched', keysB, len(selB))]:
    if keysA and ks:
        ov = len(keysA & ks)
        print(f'overlap {nm} vs canon: {ov}/{min(nA,n)} ({ov/min(nA,n)*100:.1f}%)')

print('\nclamped count-matched by year:')
print(selB.groupby(selB['entry_date'].dt.year)['pnl'].agg(['count', 'mean', 'sum']).to_string())
