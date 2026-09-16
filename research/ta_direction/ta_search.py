"""Search for technical-analysis rules that improve the D_ent canon book (user, 2026-09-16).

Guarded against data mining with three disjoint periods:
  DISCOVERY   entries 2020-07-14 .. 2022-12-31   screen + replay
  VALIDATION  entries 2023-01-01 .. 2025-12-31   frozen thresholds, must improve P&L AND Sharpe
  OOT         entries 2026                      frozen thresholds, must improve P&L AND Sharpe
(2026 was already used to confirm the k=4 / thr 0.005 cell in §0.43, so it is not pristine.)

Rules are VETOES applied to the eligible pool (GROUND >= thr) BEFORE the top-5/day selection, so a
vetoed slot is refilled by the next-ranked candidate.

Features use only bars through the prior session (Yahoo split-adjusted OHLC, SPY for market/excess),
plus today's open (gap). `ret_today` uses the entry-day vendor close and is a 15:01 proxy: flagged.
Directional features are SIGNED by the side sold (+ = favours the spread). Non-directional features
are screened per side.

    python3 research/ta_direction/ta_search.py
"""
import sys, time, warnings, argparse
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import ent_canon as ec, report_ent_canon as rec, report_mid_canon as rmc

T0 = time.time()
def log(*s): print(f'[{time.time()-T0:6.1f}s]', *s, flush=True)
pd.set_option('display.width', 260); pd.set_option('display.max_colwidth', 40)
FILL = 1.08
_ap = argparse.ArgumentParser(); _ap.add_argument('--mode', default='split', choices=['split', 'is']); MODE = _ap.parse_args().mode
DISC_END, VAL_END = pd.Timestamp('2023-01-01'), pd.Timestamp('2026-01-01')

# ── 1. candidates, canon scoring, per-candidate P&L ─────────────────────────
C = pd.read_parquet(rec.FRAME, columns=['ticker', 'entry_date', 'expiry_date', 'DTE', 'spread_type', 'entry_price', 'short_strike',
                                        'long_strike', 'model_credit', 'D_ent', 'expiry_close', 'win'])
C['entry_date'] = pd.to_datetime(C.entry_date).dt.normalize(); C['expiry_date'] = pd.to_datetime(C.expiry_date)
C['width'] = (C.short_strike - C.long_strike).abs()
CL = ec.backtest_closes()
P = ec.p_real(C, CL); C['p'], C['q'], C['ro'] = P[:, 0], P[:, 1], P[:, 2]
b = C.model_credit.values / (C.width.values - C.model_credit.values)
_, ell = ec.kelly(np.nan_to_num(C.p.values), np.nan_to_num(C.q.values), np.nan_to_num(C.ro.values), b); ell[~np.isfinite(C.p.values)] = np.nan
C['EV'] = np.exp(ell) - 1; C = C.dropna(subset=['EV']).reset_index(drop=True)
C['GROUND'] = C.EV * np.exp(-ec.K * C.D_ent)
C['credit'] = (C.model_credit * FILL).round(4); C['max_loss_adj'] = (C.width - C.credit).round(4)
C = C[C.max_loss_adj > 0].reset_index(drop=True)
bp = (C.spread_type == 'bull_put').values; sp, ss, ls, cr, ml = C.expiry_close.values, C.short_strike.values, C.long_strike.values, C.credit.values, C.max_loss_adj.values
out = np.where(bp, np.where(sp > ss, 'WIN', np.where(sp <= ls, 'LOSS', 'PARTIAL')), np.where(sp < ss, 'WIN', np.where(sp >= ls, 'LOSS', 'PARTIAL')))
pnl = np.where(bp, np.where(sp >= ss, cr, np.where(sp <= ls, -ml, cr - (ss - sp))), np.where(sp <= ss, cr, np.where(sp >= ls, -ml, cr - (sp - ss)))) * 100
pnl = np.where((out == 'PARTIAL') & (pnl > 0), pnl * 0.5, pnl) - ec.COMMISSION
C['_outcome'], C['pnl_per_contract'] = out, pnl
C['max_loss_dollar'] = C.max_loss_adj * 100; C['realize_date'] = C.expiry_date; C['entry_date_dt'] = C.entry_date
C['DKL'] = C.D_ent; C['G'] = np.log1p(C.EV); C['w_star'] = np.nan
C['side'] = np.where(bp, 1, -1)
log(f'candidates scored: {len(C):,}')

# ── 2. technical features ───────────────────────────────────────────────────
def bars(t):
    y = pd.read_csv(f'data/daily_bars_yahoo/{t}.csv'); y.columns = [c.lower() for c in y.columns]
    y['date'] = pd.to_datetime(y['date']).dt.normalize()
    return y.dropna(subset=['open', 'high', 'low', 'close']).sort_values('date').drop_duplicates('date').reset_index(drop=True)
def W(x, n): return x.ewm(alpha=1.0 / n, adjust=False).mean()
def rsi(c, n):
    ch = c.diff(); g = W(ch.clip(lower=0), n); l = W((-ch).clip(lower=0), n)
    return (100 - 100 / (1 + g / l.replace(0, np.nan))).where(l > 0, 100)

spy = bars('SPY'); sc = spy.close
spy_f = pd.DataFrame({'date': spy.date, 'spy_c': sc})
srv = np.log(sc).diff().rolling(20).std() * np.sqrt(252)
spy_f['spy_ret1z'] = (sc / sc.shift(1) - 1) / (srv / np.sqrt(252))
spy_f['spy_ret5z'] = (sc / sc.shift(5) - 1) / (srv * np.sqrt(5 / 252))
spy_f['spy_rsi2c'] = rsi(sc, 2) - 50
spy_f['spy_dsma20'] = sc / sc.rolling(20).mean() - 1
DIRS = ['ret1z', 'ret2z', 'ret5z', 'ret10z', 'ret20z', 'xs5z', 'xs20z', 'rsi2c', 'rsi14c', 'clv', 'clv3', 'dsma10', 'dsma20', 'dsma50',
        'macd_h', 'macd_slope', 'di', 'pctb', 'stoch14', 'upcount10', 'gap', 'spy_ret1z', 'spy_ret5z', 'spy_rsi2c', 'spy_dsma20', 'ret_today']
NONDIR = ['adx14', 'bbw_pct', 'atr_ratio', 'nr7', 'inside', 'rv20']
SR = ['sr10', 'sr20']   # side-aware already: + = short strike inside the recent range (cushion before the level)

def feats(y):
    c, h, l, o = y.close, y.high, y.low, y.open
    f = pd.DataFrame({'date': y.date})
    rv = np.log(c).diff().rolling(20).std() * np.sqrt(252); f['rv20'] = rv
    for n in (1, 2, 5, 10, 20): f[f'ret{n}z'] = (c / c.shift(n) - 1) / (rv * np.sqrt(n / 252))
    m = y[['date']].merge(spy_f[['date', 'spy_c']], on='date', how='left').spy_c.values
    for n in (5, 20): f[f'xs{n}z'] = ((c / c.shift(n)) - (pd.Series(m) / pd.Series(m).shift(n)).values) / (rv * np.sqrt(n / 252))
    f['rsi2c'] = rsi(c, 2) - 50; f['rsi14c'] = rsi(c, 14) - 50
    rng = (h - l).replace(0, np.nan); clv = (c - l) / rng - 0.5; f['clv'] = clv; f['clv3'] = clv.rolling(3).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1); atr = W(tr, 14)
    for n in (10, 20, 50): f[f'dsma{n}'] = (c - c.rolling(n).mean()) / atr
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean(); hist = macd - macd.ewm(span=9, adjust=False).mean()
    f['macd_h'] = hist / atr; f['macd_slope'] = hist.diff() / atr
    upm, dnm = h.diff(), -l.diff()
    pdi = 100 * W(pd.Series(np.where((upm > dnm) & (upm > 0), upm, 0.0)), 14) / atr
    ndi = 100 * W(pd.Series(np.where((dnm > upm) & (dnm > 0), dnm, 0.0)), 14) / atr
    f['di'] = pdi - ndi; f['adx14'] = W(100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan), 14)
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    f['pctb'] = (c - (mid - 2 * sd)) / (4 * sd) - 0.5
    bw = 4 * sd / mid; f['bbw_pct'] = bw.rolling(120).rank(pct=True)
    f['stoch14'] = (c - l.rolling(14).min()) / (h.rolling(14).max() - l.rolling(14).min()).replace(0, np.nan) - 0.5
    f['upcount10'] = (c.diff() > 0).rolling(10).sum() - 5
    f['atr_ratio'] = tr.rolling(5).mean() / W(tr, 20)
    f['nr7'] = ((h - l) <= (h - l).rolling(7).min()).astype(float); f['inside'] = ((h <= h.shift()) & (l >= l.shift())).astype(float)
    f['lo10'], f['hi10'] = l.rolling(10).min() / c - 1, h.rolling(10).max() / c - 1
    f['lo20'], f['hi20'] = l.rolling(20).min() / c - 1, h.rolling(20).max() / c - 1
    f['atrpct'] = atr / c
    f.loc[f.index < 60, f.columns[1:]] = np.nan
    cols = [x for x in f.columns if x != 'date']
    f[cols] = f[cols].shift(1)                                  # prior-session values on row t
    f['gap'] = (o / c.shift(1) - 1) / (atr.shift(1) / c.shift(1)); f.loc[f.index < 60, 'gap'] = np.nan
    return f

tick = sorted(C.ticker.unique()); FE = []
for i, t in enumerate(tick, 1):
    x = feats(bars(t)); x['ticker'] = t; FE.append(x)
    if i % 21 == 0 or i == len(tick): log(f'features {i}/{len(tick)} tickers ({100*i/len(tick):.0f}%)')
FE = pd.concat(FE, ignore_index=True).rename(columns={'date': 'entry_date'})
sp_prior = spy_f.copy(); sp_prior[['spy_ret1z', 'spy_ret5z', 'spy_rsi2c', 'spy_dsma20']] = sp_prior[['spy_ret1z', 'spy_ret5z', 'spy_rsi2c', 'spy_dsma20']].shift(1)
C = C.merge(FE, on=['ticker', 'entry_date'], how='left').merge(sp_prior.drop(columns='spy_c').rename(columns={'date': 'entry_date'}), on='entry_date', how='left')
prev = CL.assign(prev_close=CL.groupby('ticker').close.shift(1))[['ticker', 'date', 'prev_close']].rename(columns={'date': 'entry_date'})
C = C.merge(prev, on=['ticker', 'entry_date'], how='left')
C['ret_today'] = (C.entry_price / C.prev_close - 1) / (C.rv20 / np.sqrt(252))
ks = C.short_strike / C.entry_price - 1
C['sr10'] = np.where(C.side == 1, ks - C.lo10, C.hi10 - ks) / C.atrpct
C['sr20'] = np.where(C.side == 1, ks - C.lo20, C.hi20 - ks) / C.atrpct
for f_ in DIRS: C[f_ + '_s'] = C[f_] * C.side
log(f'features merged; coverage {100*C.ret1z.notna().mean():.1f}% of candidates')

# ── 3. selection + metrics ──────────────────────────────────────────────────
def select(D, veto=None):
    E = D[D.GROUND >= ec.THR]
    if veto is not None: E = E[~veto.loc[E.index]]
    return E.sort_values(['entry_date', 'GROUND'], ascending=[True, False]).groupby('entry_date').head(ec.TOP_N)

def metr(sel, ey):
    if len(sel) < 30: return dict(n=len(sel))
    s = rmc.build_payload(sel.sort_values('entry_date_dt').reset_index(drop=True), ey, '')['summary']
    return dict(n=len(sel), pnl=round(sel.pnl_per_contract.sum()), win=round(100 * (sel._outcome == 'WIN').mean(), 1),
                yld=round(100 * sel.pnl_per_contract.sum() / sel.max_loss_dollar.sum(), 2), sh=s['qty1_sharpe_weekly'], dd=s['qty1_max_dd'])

if MODE == 'split':
    PER = {'DISC': (C.entry_date < DISC_END) & (C.win == 'IS'), 'VAL': (C.entry_date >= DISC_END) & (C.entry_date < VAL_END), 'OOT': C.win == 'OOT'}
    EY = {'DISC': 2022, 'VAL': 2025, 'OOT': 2026}; SCREEN_YEARS = (2020, 2021, 2022); MIN_YRS = 3
else:   # user 2026-09-16: screen and replay on all of 2020-25; 2026 shown for reference only
    PER = {'DISC': C.win == 'IS', 'OOT': C.win == 'OOT'}
    EY = {'DISC': 2025, 'OOT': 2026}; SCREEN_YEARS = (2020, 2021, 2022, 2023, 2024, 2025); MIN_YRS = 5
BASE = {k: metr(select(C[m]), EY[k]) for k, m in PER.items()}
chk_is = select(C[C.win == 'IS']); chk_oot = select(C[C.win == 'OOT'])
log(f'reproduction: IS {len(chk_is)} trades ${chk_is.pnl_per_contract.sum():,.0f} (published 4,552 / $30,655); OOT {len(chk_oot)} ${chk_oot.pnl_per_contract.sum():,.0f} (594 / $4,372)')
print('\nBASELINE canon book by period:'); print(pd.DataFrame(BASE).T.to_string())

# ── 4. screen on discovery eligible pool ────────────────────────────────────
D = C[PER['DISC'] & (C.GROUND >= ec.THR)].copy(); D['yr'] = D.entry_date.dt.year  # DISC = all IS in --mode is
tests = [(f_ + '_s', 'all') for f_ in DIRS] + [(f_, s_) for f_ in NONDIR for s_ in ('bull_put', 'bear_call')] + [(f_, 'all') for f_ in SR]
rules = []
for j, (feat, grp) in enumerate(tests, 1):
    G = D if grp == 'all' else D[D.spread_type == grp]
    x = G[feat]
    if x.notna().sum() < 500: continue
    qs = {'q10': x.quantile(0.10), 'q20': x.quantile(0.20), 'q80': x.quantile(0.80), 'q90': x.quantile(0.90)}
    cand = [('<=', qs['q10'], 'bottom 10%'), ('<=', qs['q20'], 'bottom 20%'), ('>=', qs['q80'], 'top 20%'), ('>=', qs['q90'], 'top 10%')]
    if feat in ('nr7', 'inside'): cand = [('>=', 1.0, 'flag = 1')]
    for op, thr, lab in cand:
        vm = (x <= thr) if op == '<=' else (x >= thr); vm &= x.notna()
        a_, r_ = G[vm].pnl_per_contract, G[~vm & x.notna()].pnl_per_contract
        if len(a_) < 150: continue
        diff = a_.mean() - r_.mean(); t = diff / np.sqrt(a_.var() / len(a_) + r_.var() / len(r_))
        yrs = [(G[vm & (G.yr == y)].pnl_per_contract.mean() - G[~vm & x.notna() & (G.yr == y)].pnl_per_contract.mean()) for y in SCREEN_YEARS]
        rules.append(dict(feat=feat, grp=grp, op=op, thr=float(thr), lab=lab, n_veto=len(a_), veto_pnl=round(a_.mean(), 2), rest_pnl=round(r_.mean(), 2),
                          diff=round(diff, 2), t=round(t, 2), yrs=[round(v, 1) for v in yrs], all_yrs=sum(v < 0 for v in yrs) >= MIN_YRS))
    if j % 8 == 0 or j == len(tests): log(f'screened {j}/{len(tests)} feature groups ({100*j/len(tests):.0f}%)')
R = pd.DataFrame(rules).drop_duplicates(['feat', 'grp', 'op', 'thr']).reset_index(drop=True)
S1 = R[(R.t <= -2.0) & R.all_yrs].sort_values('t').reset_index(drop=True)
print(f'\n=== SCREEN ({MODE}: {len(D):,} eligible candidates): {len(R)} rules tested, {len(S1)} pass t <= -2 AND vetoed worse in >= {MIN_YRS} of {len(SCREEN_YEARS)} years ===')
print(S1[['feat', 'grp', 'lab', 'op', 'thr', 'n_veto', 'veto_pnl', 'rest_pnl', 'diff', 't', 'yrs']].to_string(index=False))

# ── 5. replay with refill on discovery, then frozen validation + OOT ───────
def mask(rule):
    x = C[rule['feat']]; vm = (x <= rule['thr']) if rule['op'] == '<=' else (x >= rule['thr'])
    vm &= x.notna()
    if rule['grp'] != 'all': vm &= (C.spread_type == rule['grp'])
    return vm
rows = []
for i, rule in S1.iterrows():
    vm = mask(rule); r = {'rule': f"{rule['feat']} {rule['grp']} {rule['op']} {rule['thr']:.3g}"}
    for k in PER:
        m = metr(select(C[PER[k]], vm[PER[k]]), EY[k]); base = BASE[k]
        r[f'{k} dP&L'] = m['pnl'] - base['pnl']; r[f'{k} dSh'] = round(m['sh'] - base['sh'], 2); r[f'{k} dDD'] = round(m['dd'] - base['dd'], 1); r[f'{k} dWin'] = round(m['win'] - base['win'], 1)
    r['disc ok'] = r['DISC dP&L'] > 0 and r['DISC dSh'] > 0
    r['PASS'] = (r['disc ok'] and r['VAL dP&L'] > 0 and r['VAL dSh'] > 0 and r['OOT dP&L'] > 0 and r['OOT dSh'] > 0) if MODE == 'split' else r['disc ok']
    rows.append(r); log(f'replayed rule {i+1}/{len(S1)} ({100*(i+1)/len(S1):.0f}%): {r["rule"]}  PASS={r["PASS"]}')
X = pd.DataFrame(rows)
print('\n=== REPLAY with refill: change vs baseline (qty1 P&L $, weekly Sharpe, maxDD pts, win pts) ===')
print(X.to_string(index=False))
PASS = S1[X.PASS.values] if len(X) else S1.iloc[0:0]
if len(PASS):
    vm = np.zeros(len(C), bool)
    for _, rule in PASS.iterrows(): vm |= mask(rule).values
    vm = pd.Series(vm, index=C.index)
    print(f'\n=== COMBINED veto of all {len(PASS)} passing rules ===')
    comb = {k: metr(select(C[PER[k]], vm[PER[k]]), EY[k]) for k in PER}
    print(pd.concat({'baseline': pd.DataFrame(BASE).T, 'combined': pd.DataFrame(comb).T}, axis=1).to_string())
else:
    print('\nNo rule passed discovery + validation + OOT.')
R.to_csv(f'research/ta_direction/ta_search_screen_{MODE}.csv', index=False); X.to_csv(f'research/ta_direction/ta_search_replay_{MODE}.csv', index=False)
log('done')
