"""Third TA search: more indicator families and more statistical power (user, 2026-09-16).

ta_search2.py tested one-feature VETOES (tails only) and nothing beat the shuffled-feature null. Vetoes
throw away most of a feature's information, so this script tries the two higher-power formulations:

  B. Single-feature continuous TILT: rank key = ln GROUND + beta * z(feature) inside the eligible pool.
     Every candidate is moved a little, not just the tails. Same family-wise within-date-shuffle null.
  C. Walk-forward COMBINED model: ridge regression on ALL features, trained only on years before the test
     year, predicting (1) per-contract P&L of eligible candidates and (2) the signed stock move to expiry
     in sigma units on all candidates. Test years 2022-2025 are genuinely out-of-sample for the model.
     The predictions tilt or veto the book. Null: shuffle features within date and rerun the whole pipeline.

New OHLC families (no volume in data/daily_bars_yahoo): overnight vs intraday return split, candle body and
wicks, engulfing / hammer / shooting-star flags, 52-week and 55-day range position, up/down streak, higher-high
count, rv5/rv20, 60d return skew and kurtosis, worst adverse daily move in 20d, 50/200d SMA trend, relative
strength vs QQQ and IWM, short-strike cushion in ATR units.
2026 is excluded: Yahoo bars are missing 2026-01-01..2026-05-14 (see ta_search2.py).

    python3 research/ta_direction/ta_search3.py [--perms 100]
"""
import sys, argparse
_ap = argparse.ArgumentParser(); _ap.add_argument('--perms', type=int, default=100); NP3 = _ap.parse_args().perms
sys.argv = ['ta_search2.py', '--perms', '0']
exec(compile(open('research/ta_direction/ta_search2.py').read().split('# ── permutation null')[0], 'ta_search2.py[1-3]', 'exec'))

# ── extra OHLC features, prior session only ─────────────────────────────────────────────────────────
qqq, iwm = bars('QQQ').set_index('date').close, bars('IWM').set_index('date').close
def extra(y):
    c, h, l, o = y.close, y.high, y.low, y.open; f = pd.DataFrame({'date': y.date})
    lr = np.log(c).diff(); sd = lr.rolling(20).std(); rv = sd * np.sqrt(252)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1); atr = W(tr, 14)
    rng = (h - l).replace(0, np.nan)
    on, intra = np.log(o / c.shift()), np.log(c / o)
    f['on1z'], f['in1z'] = on / sd, intra / sd
    f['on5z'], f['in5z'] = on.rolling(5).sum() / (sd * np.sqrt(5)), intra.rolling(5).sum() / (sd * np.sqrt(5))
    f['body'] = (c - o) / rng; f['wick'] = ((np.minimum(o, c) - l) - (h - np.maximum(o, c))) / rng
    po, pc = o.shift(), c.shift()
    f['engulf'] = np.where((c > o) & (pc < po) & (c >= po) & (o <= pc), 1.0, np.where((c < o) & (pc > po) & (c <= po) & (o >= pc), -1.0, 0.0))
    lw, uw, ab = (np.minimum(o, c) - l) / rng, (h - np.maximum(o, c)) / rng, (c - o).abs() / rng
    f['hammer'] = np.where((lw >= 0.6) & (ab <= 0.3), 1.0, np.where((uw >= 0.6) & (ab <= 0.3), -1.0, 0.0))
    for n in (55, 250):
        lo_, hi_ = l.rolling(n, min_periods=int(n * 0.8)).min(), h.rolling(n, min_periods=int(n * 0.8)).max()
        f[f'pos{n}'] = (c - lo_) / (hi_ - lo_).replace(0, np.nan) - 0.5
    up = np.sign(c.diff()).fillna(0); grp = (up != up.shift()).cumsum(); f['streak'] = up * up.groupby(grp).cumcount().add(1)
    f['hhll5'] = (h > h.shift()).rolling(5).sum() - (l < l.shift()).rolling(5).sum()
    f['rv5_20'] = lr.rolling(5).std() / sd
    f['skew60'], f['kurt60'] = lr.rolling(60).skew(), lr.rolling(60).kurt()
    f['maxdn20'], f['maxup20'] = (lr / sd.shift()).rolling(20).min(), (lr / sd.shift()).rolling(20).max()
    s50, s200 = c.rolling(50).mean(), c.rolling(200, min_periods=160).mean()
    f['dsma200'] = (c - s200) / atr; f['trend50_200'] = s50 / s200 - 1
    q_ = pd.Series(qqq.reindex(y.date).values); i_ = pd.Series(iwm.reindex(y.date).values)
    f['xq5z'] = (c / c.shift(5) - q_ / q_.shift(5)) / (sd * np.sqrt(5)); f['xi5z'] = (c / c.shift(5) - i_ / i_.shift(5)) / (sd * np.sqrt(5))
    f['atr_abs'] = atr
    f.loc[f.index < 60, f.columns[1:]] = np.nan
    cols = [x for x in f.columns if x != 'date']; f[cols] = f[cols].shift(1)
    return f
EX = []
for i, t in enumerate(tick, 1):
    x = extra(bars(t)); x['ticker'] = t; EX.append(x)
    if i % 28 == 0 or i == len(tick): log(f'extra features {i}/{len(tick)} tickers ({100*i/len(tick):.0f}%)')
C = C.merge(pd.concat(EX).rename(columns={'date': 'entry_date'}), on=['ticker', 'entry_date'], how='left')
C['cushion_atr'] = C.side * (C.entry_price - C.short_strike) / C.atr_abs
C['adverse20'] = np.where(C.side == 1, C.maxdn20, -C.maxup20)          # worst daily move against the side, sigma
NEW_DIR = ['on1z', 'in1z', 'on5z', 'in5z', 'body', 'wick', 'engulf', 'hammer', 'pos55', 'pos250', 'streak', 'hhll5', 'skew60',
           'dsma200', 'trend50_200', 'xq5z', 'xi5z']
for f_ in NEW_DIR: C[f_ + '_s'] = C[f_] * C.side
NEW_ND = ['rv5_20', 'kurt60', 'cushion_atr', 'adverse20']
DIR_ALL = [f + '_s' for f in DIRS] + ['peer_ret1z_s', 'peer_ret5z_s', 'own_minus_peer1_s'] + SR + [f + '_s' for f in NEW_DIR]
ND_ALL = NONDIR + ['peer_corr'] + NEW_ND
for f_ in ND_ALL: C[f_ + '_xs'] = C[f_] * C.side                         # lets the linear model treat puts and calls differently
MODEL_X = DIR_ALL + ND_ALL + [f + '_xs' for f in ND_ALL]
C['rid'] = np.arange(len(C)); C['side_only'] = C.side.astype(float)
IS = pool('IS'); PNL, WK, NW, YR = IS.pnl_per_contract.values, IS.wk.values, IS.wk.max() + 1, IS.yr.values
base_sel = book(IS, np.zeros(len(IS), bool)); base_w = np.bincount(WK, PNL * base_sel, NW)
assert base_sel.sum() == 4554
npd = IS.groupby('dcode').size()
log(f'{len(MODEL_X)} features; eligible per day: median {npd.median():.0f}, days with > {ec.TOP_N} eligible {100*(npd > ec.TOP_N).mean():.0f}%, '
    f'surplus candidates {int((npd - ec.TOP_N).clip(lower=0).sum()):,} (the only room any filter has to swap trades)')

def book_key(E, key, veto=None):
    """Top-N per day by `key` (desc) after veto; returns boolean selection aligned to E."""
    o = np.lexsort((-key, E.dcode.values)); keep = np.ones(len(E), bool) if veto is None else ~veto
    ko = keep[o]; cs = np.cumsum(ko); d = E.dcode.values[o]; st = np.flatnonzero(np.r_[1, np.diff(d)])
    rank = cs - np.repeat(np.r_[0, cs][st], np.diff(np.r_[st, len(E)]))
    sel = np.zeros(len(E), bool); sel[o] = ko & (rank <= ec.TOP_N); return sel
def zs(x, ref):
    m, s = np.nanmean(ref), np.nanstd(ref); z = (x - m) / (s if s > 0 else 1); return np.clip(np.nan_to_num(z), -3, 3)
LG = np.log(IS.GROUND.values)
assert (book_key(IS, LG) == base_sel).all()

# ── B. single-feature continuous tilt ──────────────────────────────────────────────────────────────
BETAS = (-0.5, -0.2, 0.2, 0.5)
C_SIDE = 'side_only'                                                     # control: pure put/call preference, no indicator
TILT_FEATS = DIR_ALL + [f for g in ND_ALL for f in (g, g + '_xs')] + [C_SIDE]
def tilt_scores(E):
    out = {}
    for f in TILT_FEATS:
        z = zs(E[f].values, E[f].values)
        for b_ in BETAS: out[(f, b_)] = book_key(E, LG + b_ * z)
    return out
def score(sel):
    d = np.bincount(WK, PNL * sel, NW) - base_w; return d.mean() / (d.std(ddof=1) / np.sqrt(NW)) if d.std() > 0 else 0.0
TB = []
for (f, b_), sel in tilt_scores(IS).items():
    yrs = [PNL[sel & (YR == y)].sum() - PNL[base_sel & (YR == y)].sum() for y in range(2020, 2026)]
    TB.append(dict(feat=f, beta=b_, dPnL=round(PNL[sel].sum() - PNL[base_sel].sum()), t=round(score(sel), 2),
                   yrs_up=sum(v > 0 for v in yrs), swapped=int((sel != base_sel).sum() // 2), yrs=[round(v) for v in yrs]))
TB = pd.DataFrame(TB); log(f'B: scored {len(TB)} tilts')

# ── C. walk-forward ridge ──────────────────────────────────────────────────────────────────────────
TEST_YEARS = (2022, 2023, 2024, 2025); LAM = 50.0
CY = C.entry_date.dt.year.values
C['mv_z'] = C.side * np.log(C.expiry_close / C.entry_price) / (C.rv20 * np.sqrt(C.DTE.clip(lower=1) / 365))
def ridge(X, y, lam):
    m, s = X.mean(0), X.std(0); s[s == 0] = 1; Z = (X - m) / s
    w = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean())); return lambda Xn: (Xn - m) / s @ w + y.mean()
def feat_mat(D): return np.nan_to_num(np.clip(D[MODEL_X].values.astype(float), -50, 50))
def walk_forward(Cf, ISf):
    """Out-of-sample predictions for ISf rows in TEST_YEARS from models trained on strictly earlier years."""
    pred = {'pnl': np.full(len(ISf), np.nan), 'move': np.full(len(ISf), np.nan)}
    Xc, Xi = feat_mat(Cf), feat_mat(ISf); yrs_i = ISf.yr.values
    elig = (Cf.GROUND >= ec.THR).values & (Cf.win == 'IS').values
    ok_mv = np.isfinite(Cf.mv_z.values) & (Cf.win == 'IS').values
    for Y in TEST_YEARS:
        tr = CY < Y
        for key, rows, y in (('pnl', tr & elig, Cf.pnl_per_contract.values), ('move', tr & ok_mv, np.clip(Cf.mv_z.values, -5, 5))):
            f = ridge(Xc[rows], y[rows], LAM); tm = yrs_i == Y
            p_tr = f(Xc[rows]); pred[key][tm] = (f(Xi[tm]) - p_tr.mean()) / p_tr.std()
    return pred
TMASK = np.isin(YR, TEST_YEARS)
def c_variants(pred):
    out = {}
    for key in ('pnl', 'move'):
        z = np.nan_to_num(pred[key])
        for b_ in (0.25, 0.5, 1.0): out[f'{key} tilt beta {b_}'] = book_key(IS, LG + b_ * z)
        for q in (0.10, 0.20): out[f'{key} veto worst {int(q*100)}%'] = book_key(IS, LG, TMASK & (z <= np.quantile(z[TMASK], q)))
    return out
def score_test(sel):
    d = (np.bincount(WK, PNL * sel * TMASK, NW) - np.bincount(WK, PNL * base_sel * TMASK, NW))[np.unique(WK[TMASK])]
    return d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if d.std() > 0 else 0.0
PRED = walk_forward(C, IS)
TC = []
for name, sel in c_variants(PRED).items():
    yrs = [PNL[sel & (YR == y)].sum() - PNL[base_sel & (YR == y)].sum() for y in TEST_YEARS]
    TC.append(dict(variant=name, dPnL_2022_25=round(sum(yrs)), t=round(score_test(sel), 2), yrs_up=sum(v > 0 for v in yrs),
                   swapped=int((sel & TMASK != base_sel & TMASK).sum() // 2), yrs=[round(v) for v in yrs]))
TC = pd.DataFrame(TC)
for key in ('pnl', 'move'):
    z = PRED[key][TMASK]; ic = pd.Series(z).rank().corr(pd.Series(PNL[TMASK]).rank())
    log(f'C: out-of-sample rank IC of {key} model vs realized per-contract P&L, 2022-25 eligible pool: {ic:+.3f}')

# ── shared null: shuffle every feature row within entry date (C and IS consistently), rerun B and C ─────────
rng = np.random.default_rng(916); NB, NC = [], []
FC = sorted(set(MODEL_X) | (set(TILT_FEATS) - {C_SIDE}))
cd = C.entry_date.factorize()[0]; order_c = np.argsort(cd, kind='stable'); st_c = np.flatnonzero(np.r_[1, np.diff(cd[order_c])])
pos = IS.rid.values
for p in range(NP3):
    perm = order_c.copy()
    for a, b in zip(st_c, np.r_[st_c[1:], len(C)]): perm[a:b] = order_c[a:b][rng.permutation(b - a)]
    Cp = C.copy()
    shuf = np.empty(len(C), int); shuf[order_c] = perm                  # row order_c[i] receives features of perm[i]
    Cp[FC] = C[FC].values[shuf]
    ISp = IS.copy(); ISp[FC] = Cp[FC].values[pos]
    NB.append(max(score(s) for (f_, _), s in tilt_scores(ISp).items() if f_ != C_SIDE))
    NC.append(max(score_test(s) for s in c_variants(walk_forward(Cp, ISp)).values()))
    if (p + 1) % 10 == 0 or p + 1 == NP3: log(f'null {p+1}/{NP3} ({100*(p+1)/NP3:.0f}%)')
NB, NC = np.array(NB), np.array(NC)
TB['p_fwer'] = [(NB >= t).mean() for t in TB.t]; TC['p_fwer'] = [(NC >= t).mean() for t in TC.t]

pd.set_option('display.width', 250)
print(f'\n=== B. single-feature tilt, IS 2020-25: {len(TB)} tilts. Null max t: median {np.median(NB):.2f}, 95th {np.quantile(NB, .95):.2f} ===')
print(TB.sort_values('t', ascending=False).head(20).to_string(index=False))
print('\ncontrol, side preference only (no indicator):'); print(TB[TB.feat == C_SIDE].to_string(index=False))
print(f'\n=== C. walk-forward ridge, all {len(MODEL_X)} features, test years 2022-25 only. Null max t: median {np.median(NC):.2f}, 95th {np.quantile(NC, .95):.2f} ===')
print(TC.sort_values('t', ascending=False).to_string(index=False))
TB.drop(columns='yrs').to_csv('research/ta_direction/ta_search3_tilt.csv', index=False)
TC.drop(columns='yrs').to_csv('research/ta_direction/ta_search3_walkforward.csv', index=False)
log('done')
