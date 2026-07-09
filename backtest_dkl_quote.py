"""Two-channel GROUND: Gamma = G * exp(-k1*DKL_prob - k2*DKL_quote).

DKL_quote = Bernoulli KL between the loss probabilities implied by the
LAST-based and MID-based credits (c/width each). Zero when LAST and MID
agree on the spread's value; large when a stale print inflates the credit.
Selection level (b from LAST) is untouched — only confidence is discounted.

Protocol: fit k2 on 2020-2024 (count-matched threshold per k2), validate
on 2025 with the train-fitted threshold. Canon (k2=0, thr=0.075) is the
baseline in both windows. Realization 0.80 x raw_last both arms. Read-only.
"""
import math
import numpy as np
import pandas as pd

K1 = 10.0
THR_CANON = 0.075
LAST_PCT = 0.80
SPLIT = pd.Timestamp('2025-01-01')

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
df = df.dropna(subset=['p', 'q', 'ro', 'G', 'DKL', 'raw_last', 'expiry_close',
                       'short_bid', 'short_ask', 'long_bid', 'long_ask']).copy()
df['entry_date'] = pd.to_datetime(df['entry_date'])
print(f'{len(df)} candidates, {df.entry_date.min().date()} -> {df.entry_date.max().date()}')

# implied P(loss) from each credit basis
c_last = df['net_credit'].to_numpy()
c_mid = ((df['short_bid'] + df['short_ask']) / 2
         - (df['long_bid'] + df['long_ask']) / 2).to_numpy()
w = df['spread_width'].to_numpy()
EPS = 1e-4
p1 = np.clip(c_last / w, EPS, 1 - EPS)
p2 = np.clip(c_mid / w, EPS, 1 - EPS)
df['DKL_quote'] = p1 * np.log(p1 / p2) + (1 - p1) * np.log((1 - p1) / (1 - p2))
print('DKL_quote: p50=%.4f p90=%.4f p99=%.4f  (zero-ish <1e-4: %.1f%%)' % (
    df['DKL_quote'].quantile(.5), df['DKL_quote'].quantile(.9),
    df['DKL_quote'].quantile(.99), (df['DKL_quote'] < 1e-4).mean() * 100))


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
df['GAMMA0'] = df['G'] / np.exp(K1 * df['DKL'])


def select(d, col, thr):
    q = d[d[col].notna() & (d[col] >= thr)]
    return (q.sort_values(['entry_date', col], ascending=[True, False])
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


def show(label, s):
    print(f'{label:42s} n={s["n"]:5d} avg=${s["avg"]:7.2f} total=${s["tot"]:9.0f} '
          f'WR={s["wr"]:4.1f}% Sh(wk)={s["sh"]:5.2f}')


train = df[df['entry_date'] < SPLIT]
test = df[df['entry_date'] >= SPLIT]
print(f'train {train.entry_date.min().date()}->{train.entry_date.max().date()} '
      f'({len(train)}), test {test.entry_date.min().date()}->{test.entry_date.max().date()} '
      f'({len(test)})')

selA_tr = select(train, 'GAMMA0', THR_CANON)
n_target = len(selA_tr)
print('\n=== TRAIN 2020-2024 ===')
show('canon k2=0, thr=0.075', stats(selA_tr))

results = []
for k2 in [0.5, 1, 2, 5, 10, 20, 50, 100]:
    col = f'GM_{k2}'
    df[col] = df['GAMMA0'] * np.exp(-k2 * df['DKL_quote'])
    tr = df[df['entry_date'] < SPLIT]
    lo, hi = 0.0, 0.2
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if len(select(tr, col, mid)) > n_target: lo = mid
        else: hi = mid
    thr = 0.5 * (lo + hi)
    s = stats(select(tr, col, thr))
    show(f'k2={k2:<5} thr={thr:.4f} (count-matched)', s)
    results.append((k2, thr, s))

test = df[df['entry_date'] >= SPLIT]
print('\n=== TEST 2025 (train-fitted thresholds, untouched) ===')
selA_te = select(test, 'GAMMA0', THR_CANON)
show('canon k2=0, thr=0.075', stats(selA_te))
keysA = set(zip(selA_te['entry_date'], selA_te['ticker'],
                selA_te['short_strike'], selA_te['spread_type']))
for k2, thr, _ in results:
    sel = select(test, f'GM_{k2}', thr)
    s = stats(sel)
    ks = set(zip(sel['entry_date'], sel['ticker'], sel['short_strike'], sel['spread_type']))
    ov = len(keysA & ks)
    show(f'k2={k2:<5} thr={thr:.4f}', s)
    print(f'{"":42s} overlap with canon-2025: {ov}/{len(keysA)}')

df[['entry_date', 'ticker', 'short_strike', 'spread_type', 'G', 'DKL', 'DKL_quote',
    'GAMMA0', 'pnl', 'oc']].to_csv('/tmp/dkl_quote_scores.csv', index=False)
print('\nwrote /tmp/dkl_quote_scores.csv')
