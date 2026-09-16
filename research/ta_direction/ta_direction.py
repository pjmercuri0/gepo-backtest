"""Technical-analysis direction calls vs the D_ent canon book (user, 2026-09-16).

Signals are built from data/daily_bars_yahoo/<T>.csv (split-adjusted OHLC; daily returns match the
vendor closes on 99.9% of days) using ONLY bars through the PRIOR session s = t-1, plus today's open
for the gap signal. Nothing uses the entry-day close.

Each signal returns +1 (call up), -1 (call down) or 0 (no call):
  rsi2      2-day RSI < 10 -> up, > 90 -> down                       (short-term reversal)
  streak3   >= 3 consecutive down closes -> up, >= 3 up -> down      (short-term reversal)
  clv       close in bottom 20% of the day's range -> up, top 20% -> down (reversal)
  trend     close above SMA20 and SMA50 with ADX14 > 25 -> up; below both with ADX > 25 -> down
  breakout  close above prior 20-day high -> up; below prior 20-day low -> down
  macd      MACD(12,26,9) histogram > 0 and rising -> up; < 0 and falling -> down
  rs_spy    5d AND 20d return in excess of SPY both > 0 -> up; both < 0 -> down
  gap       today's open vs prior close > +0.5 ATR14 -> up; < -0.5 ATR -> down (continuation)

Step 1  hit rate: over every unique (ticker, entry, expiry) candidate, how often the stock finished
        in the called direction at expiry (vendor raw closes), vs the unconditional rate of that
        direction on the same rows. Per year, because same-day calls across names are correlated.
Step 2  agree / disagree split on the canon book (report_ent_canon.select): agree = bull_put with an
        up call or bear_call with a down call.

    python3 research/ta_direction/ta_direction.py [--win IS|OOT]
"""
import sys, time, argparse, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import report_ent_canon as rec

ap = argparse.ArgumentParser(); ap.add_argument('--win', default='IS'); a = ap.parse_args()
T0 = time.time()
def log(*s): print(f'[{time.time()-T0:5.1f}s]', *s, flush=True)
pd.set_option('display.width', 250)
SIGS = ['rsi2', 'streak3', 'clv', 'trend', 'breakout', 'macd', 'rs_spy', 'gap']


def bars(t):
    y = pd.read_csv(f'data/daily_bars_yahoo/{t}.csv'); y.columns = [c.lower() for c in y.columns]
    y['date'] = pd.to_datetime(y['date']).dt.normalize()
    return y.dropna(subset=['open', 'high', 'low', 'close']).sort_values('date').drop_duplicates('date').reset_index(drop=True)


def wilder(x, n): return x.ewm(alpha=1.0 / n, adjust=False).mean()


def signals(y, spy):
    c, h, l, o = y.close, y.high, y.low, y.open
    d = pd.DataFrame({'date': y.date})
    ch = c.diff()
    rs = wilder(ch.clip(lower=0), 2) / wilder((-ch).clip(lower=0), 2).replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs); rsi[wilder((-ch).clip(lower=0), 2) == 0] = 100
    d['rsi2'] = np.select([rsi < 10, rsi > 90], [1, -1], 0)
    dn = (ch < 0).astype(int); up = (ch > 0).astype(int)
    run_dn = dn.groupby((dn != dn.shift()).cumsum()).cumsum() * dn
    run_up = up.groupby((up != up.shift()).cumsum()).cumsum() * up
    d['streak3'] = np.select([run_dn >= 3, run_up >= 3], [1, -1], 0)
    rng = (h - l).replace(0, np.nan); clv = (c - l) / rng
    d['clv'] = np.select([clv < 0.2, clv > 0.8], [1, -1], 0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(tr, 14)
    upm = h.diff(); dnm = -l.diff()
    pdm = np.where((upm > dnm) & (upm > 0), upm, 0.0); ndm = np.where((dnm > upm) & (dnm > 0), dnm, 0.0)
    pdi = 100 * wilder(pd.Series(pdm), 14) / atr; ndi = 100 * wilder(pd.Series(ndm), 14) / atr
    adx = wilder(100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan), 14)
    s20, s50 = c.rolling(20).mean(), c.rolling(50).mean()
    d['trend'] = np.select([(c > s20) & (c > s50) & (adx > 25), (c < s20) & (c < s50) & (adx > 25)], [1, -1], 0)
    hh = h.shift(1).rolling(20).max(); ll = l.shift(1).rolling(20).min()
    d['breakout'] = np.select([c > hh, c < ll], [1, -1], 0)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    d['macd'] = np.select([(hist > 0) & (hist > hist.shift()), (hist < 0) & (hist < hist.shift())], [1, -1], 0)
    m = y[['date']].merge(spy, on='date', how='left')
    x5 = c / c.shift(5) - (m.spy / m.spy.shift(5)).values; x20 = c / c.shift(20) - (m.spy / m.spy.shift(20)).values
    d['rs_spy'] = np.select([(x5 > 0) & (x20 > 0), (x5 < 0) & (x20 < 0)], [1, -1], 0)
    warm = pd.Series(np.arange(len(y)) < 60)
    for sname in SIGS[:-1]:
        d.loc[warm, sname] = 0
    # everything above is known at the CLOSE of `date`; shift so row t carries the prior session's call
    d[SIGS[:-1]] = d[SIGS[:-1]].shift(1).fillna(0).astype(int)
    gap = (o / c.shift(1) - 1) / (atr.shift(1) / c.shift(1))          # today's open vs prior close, prior ATR
    d['gap'] = np.select([gap > 0.5, gap < -0.5], [1, -1], 0); d.loc[warm, 'gap'] = 0
    return d


# ── build signals ───────────────────────────────────────────────────────────
F = pd.read_parquet(rec.FRAME, columns=['ticker', 'entry_date', 'expiry_date', 'DTE', 'entry_price', 'expiry_close', 'win'])
F = F[F.win == a.win].copy(); F['entry_date'] = pd.to_datetime(F.entry_date).dt.normalize()
spy = bars('SPY')[['date', 'close']].rename(columns={'close': 'spy'})
S = []
tickers = sorted(F.ticker.unique())
for i, t in enumerate(tickers, 1):
    s = signals(bars(t), spy); s['ticker'] = t; S.append(s)
    if i % 20 == 0 or i == len(tickers): log(f'signals {i}/{len(tickers)} tickers ({100*i/len(tickers):.0f}%)')
S = pd.concat(S, ignore_index=True).rename(columns={'date': 'entry_date'})

# ── step 1: hit rate on every candidate ─────────────────────────────────────
U = F.drop_duplicates(['ticker', 'entry_date', 'expiry_date']).merge(S, on=['ticker', 'entry_date'], how='inner')
U['move'] = np.sign(U.expiry_close / U.entry_price - 1)
U = U[U.move != 0].copy(); U['year'] = U.entry_date.dt.year
base_up = (U.move > 0).mean()
log(f'{a.win}: {len(U):,} unique candidate ticker-days with signals; unconditional up rate {100*base_up:.1f}%')
rows = []
for sname in SIGS:
    r = {'signal': sname}
    for side, lab in ((1, 'up'), (-1, 'down')):
        g = U[U[sname] == side]; base = base_up if side == 1 else 1 - base_up
        hit = (g.move == side).mean() if len(g) else np.nan
        r[f'{lab} n'] = len(g); r[f'{lab} hit%'] = round(100 * hit, 1); r[f'{lab} lift'] = round(100 * (hit - base), 1)
    g = U[U[sname] != 0]
    hit = (g.move == g[sname]).mean(); base = np.where(g[sname] == 1, base_up, 1 - base_up).mean()
    r['all lift'] = round(100 * (hit - base), 1); r['coverage%'] = round(100 * len(g) / len(U), 1)
    yr = []
    for yy, gy in g.groupby('year'):
        by = (U[U.year == yy].move > 0).mean()
        yr.append(round(100 * ((gy.move == gy[sname]).mean() - np.where(gy[sname] == 1, by, 1 - by).mean()), 1))
    r['lift by year (vs that year\'s base)'] = yr; r['years +'] = f'{sum(x > 0 for x in yr)}/{len(yr)}'
    rows.append(r)
print(f'\n=== STEP 1 · {a.win} · direction to expiry: hit rate of the call vs the unconditional rate of that direction (pts) ===')
print(pd.DataFrame(rows).to_string(index=False))

# ── step 2: agree / disagree on the canon book ──────────────────────────────
log('selecting the canon book (full-session P_real, k=4, thr 0.005, fill 1.08)')
B = rec.select(a.win)
B['entry_date'] = pd.to_datetime(B.entry_date).dt.normalize()
B = B.merge(S, on=['ticker', 'entry_date'], how='left'); B[SIGS] = B[SIGS].fillna(0).astype(int)
B['side'] = np.where(B.spread_type == 'bull_put', 1, -1)
B['positive'] = (B._outcome == 'WIN') | ((B._outcome == 'PARTIAL') & (B.pnl_per_contract > 0))
tot = B.pnl_per_contract.sum()
log(f'canon book {len(B):,} trades, win {100*(B._outcome=="WIN").mean():.1f}%, P&L/contract ${B.pnl_per_contract.mean():.2f}')
rows = []
def cell(g):
    if len(g) == 0: return dict(n=0)
    return dict(n=len(g), win=round(100 * (g._outcome == 'WIN').mean(), 1), pos=round(100 * g.positive.mean(), 1),
                pnl_tr=round(g.pnl_per_contract.mean(), 2), yld=round(100 * g.pnl_per_contract.sum() / g.max_loss_dollar.sum(), 2))
for sname in SIGS:
    ag = B[B[sname] == B.side]; dg = B[B[sname] == -B.side]; nu = B[B[sname] == 0]
    r = {'signal': sname}
    for lab, g in (('AGREE', ag), ('DISAGREE', dg), ('no call', nu)):
        c = cell(g); r.update({f'{lab} {k}': v for k, v in c.items()})
    yr = []
    for yy, gy in B.groupby(B.entry_date.dt.year):
        a_ = gy[gy[sname] == gy.side]; d_ = gy[gy[sname] == -gy.side]
        yr.append(round(a_.pnl_per_contract.mean() - d_.pnl_per_contract.mean(), 1) if len(a_) >= 10 and len(d_) >= 10 else None)
    r['agree-disagree $/tr by year'] = yr
    r['drop DISAGREE: book P&L'] = round(tot - dg.pnl_per_contract.sum())
    rows.append(r)
R = pd.DataFrame(rows)
print(f'\n=== STEP 2 · {a.win} · canon book split by whether the signal agrees with the side sold (qty1, per contract) ===')
print(f'book: n={len(B)}  win={100*(B._outcome=="WIN").mean():.1f}%  pos={100*B.positive.mean():.1f}%  $/tr={B.pnl_per_contract.mean():.2f}  P&L=${tot:,.0f}')
print(R.to_string(index=False))
U.to_parquet(f'research/ta_direction/signals_candidates_{a.win}.parquet'); B.drop(columns=[c for c in B.columns if B[c].dtype == object and c not in ('ticker', 'spread_type', '_outcome')]).to_parquet(f'research/ta_direction/signals_book_{a.win}.parquet')
log('done')
