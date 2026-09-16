"""2026 test of the market-gap drift forecast (user, 2026-09-16). Pre-specified before looking at 2026:

  forecast   z_hat(date) = beta * zscore(mean over names of the opening gap in ATR units)
             beta, mean, sd fitted on ALL IS stock-days 2020-07..2025-12, then frozen
  into P_real as drift mu = gamma * z_hat * sigma_daily * sqrt(DTE sessions), gamma in {1, 3} (both reported)
  also the SPY-only gap version (mkt_spy_gapz), same protocol.

2026 opens come from a fresh Yahoo chart-API pull saved in the session scratchpad (data/daily_bars_yahoo is
missing 2026-01-01..05-14 and is NOT modified). Closes matched the existing files exactly on the 2025 overlap.

    python3 research/ta_direction/ta_predict_oot.py <bars2026_dir>
"""
import sys, os
BARS26 = sys.argv[1]
sys.argv = ['ta_predict_mkt.py', '--perms', '0']
exec(compile(open('research/ta_direction/ta_predict_mkt.py').read().split('RES = run_mkt(MKV)')[0], 'ta_predict_mkt.py[1-5]', 'exec'))

def gap_series(y):
    c, h, l, o = y.close, y.high, y.low, y.open
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1); atr = W(tr, 14)
    return pd.DataFrame({'entry_date': y.date, 'gap': ((o / c.shift() - 1) / (atr.shift() / c.shift())).values})
G26 = []
for t in tick + ['SPY']:
    f = f'{BARS26}/{t}.csv'
    if not os.path.exists(f): continue
    y = pd.read_csv(f).dropna(); y['date'] = pd.to_datetime(y.date).dt.normalize(); y = y.sort_values('date').drop_duplicates('date').reset_index(drop=True)
    g = gap_series(y); g['ticker'] = t; G26.append(g)
G26 = pd.concat(G26)
chk = G26[(G26.entry_date.dt.year == 2025) & (G26.ticker != 'SPY') & (G26.entry_date >= '2025-03-01')].merge(FE[['ticker', 'entry_date', 'gap']], on=['ticker', 'entry_date'])
log(f'gap from fresh bars vs existing features, 2025-03..12: n={len(chk)}, median abs diff {np.nanmedian(np.abs(chk.gap_x - chk.gap_y)):.4f}, corr {chk.gap_x.corr(chk.gap_y):.4f}')

# market features per date: IS from existing features (as in the search), 2026 from the fresh bars
mk_is = MK[['mkt_gap', 'mkt_spy_gapz']]
names26 = G26[(G26.ticker != 'SPY') & (G26.entry_date >= '2026-01-01')].groupby('entry_date').gap.mean().rename('mkt_gap')
sp26 = pd.read_csv(f'{BARS26}/SPY.csv').dropna(); sp26['date'] = pd.to_datetime(sp26.date); sp26 = sp26.sort_values('date').reset_index(drop=True)
sd26 = np.log(sp26.close).diff().rolling(20).std().shift()
spyg = pd.Series(((sp26.open / sp26.close.shift() - 1) / sd26).values, index=sp26.date, name='mkt_spy_gapz')
mk26 = pd.concat([names26, spyg[spyg.index >= '2026-01-01']], axis=1)
MKALL = pd.concat([mk_is[mk_is.index < '2026-01-01'], mk26]).sort_index()
ed = C.entry_date.values.astype('datetime64[ns]'); ix = MKALL.index.values.astype('datetime64[ns]')
pp = np.clip(np.searchsorted(ix, ed), 0, len(ix) - 1); hit = ix[pp] == ed
OOTM = (C.win == 'OOT').values
log(f'2026 candidates with a market gap value: {100*hit[OOTM].mean():.1f}%')

def book_mask(G, M):
    key = np.where(np.isfinite(G) & (G >= ec.THR) & M, G, -np.inf)
    o = np.lexsort((-key, dc)); d = dc[o]; st = np.flatnonzero(np.r_[1, np.diff(d)])
    rank = np.arange(len(o)) - np.repeat(st, np.diff(np.r_[st, len(o)])) + 1
    sel = np.zeros(len(C), bool); sel[o] = (rank <= ec.TOP_N) & np.isfinite(key[o]); return sel
B_OOT = book_mask(G0v, OOTM)
log(f'canon 2026 reproduced: {B_OOT.sum()} trades ${PN[B_OOT].sum():,.0f} (published 594 / $4,372)')

rows = []
for feat in ('mkt_gap', 'mkt_spy_gapz'):
    x = np.full(len(C), np.nan); x[hit] = MKALL[feat].values[pp[hit]]
    tr = TRAIN_OK & np.isfinite(x)                                  # all IS stock-days 2020-25
    m, s = x[tr].mean(), x[tr].std(); z = np.clip((x - m) / s, -4, 4); yc = Y[tr] - Y[tr].mean()
    beta = (z[tr] @ yc) / (z[tr] @ z[tr])
    log(f'{feat}: frozen beta {beta:+.4f} sigma per z (IS n={tr.sum():,})')
    for gm in (1.0, 3.0):
        mu = np.nan_to_num(gm * beta * z) * SDD * np.sqrt(DSESS)
        sel = book_mask(ground(np.where(OOTM, mu, 0.0)), OOTM)
        mo, mb = metr(C[sel], 2026), metr(C[B_OOT], 2026)
        rows.append(dict(forecast=feat, gamma=gm, n=mo['n'], pnl=mo['pnl'], win=mo['win'], yld=mo['yld'], sh=mo['sh'], dd=mo['dd'],
                         dPnL=mo['pnl'] - mb['pnl'], dSh=round(mo['sh'] - mb['sh'], 2), new_trades=int((sel & ~B_OOT).sum()),
                         bull_put_share=round(100 * (C.spread_type.values[sel] == 'bull_put').mean(), 1)))
base = metr(C[B_OOT], 2026)
pd.set_option('display.width', 250)
print(f"\n=== 2026 (Jan 1 - Aug 20 entries), frozen IS fit. canon: n {base['n']} P&L ${base['pnl']:,} win {base['win']} yield {base['yld']} Sh {base['sh']} DD {base['dd']}, "
      f"bull_put share {100*(C.spread_type.values[B_OOT]=='bull_put').mean():.1f}% ===")
print(pd.DataFrame(rows).to_string(index=False))
log('done')

# ── diagnostics ──
x = np.full(len(C), np.nan); x[hit] = MKALL['mkt_gap'].values[pp[hit]]
print('mkt_gap by year (per date): ', MKALL.mkt_gap.groupby(MKALL.index.year).agg(['mean', 'std', 'count']).round(3).to_dict('index'))
print('2026 mkt_gap by month:', MKALL.mkt_gap[MKALL.index >= '2026-01-01'].groupby(MKALL.index[MKALL.index >= '2026-01-01'].month).agg(['mean', 'std', 'count']).round(3).to_dict('index'))
t0 = triple(np.zeros(len(C)))
for lab, M in (('IS 2022-25', TEST), ('2026', OOTM)):
    elig = np.isfinite(G0v) & (G0v >= ec.THR) & M
    print(lab, 'eligible per day', round(elig.sum() / len(np.unique(dc[M])), 2), ' candidates within 20% of THR:', round(100 * ((G0v > 0.8 * ec.THR) & (G0v < 1.25 * ec.THR) & M).sum() / M.sum(), 1), '%',
          ' SDD median', round(np.nanmedian(SDD[M]), 4), ' DSESS mean', round(DSESS[M].mean(), 2))

# ── walk-forward 2022-25 with the same forecast, published-style metrics per year and pooled ──
x = np.full(len(C), np.nan); x[hit] = MKALL['mkt_gap'].values[pp[hit]]
WF = np.zeros(len(C))
for Yr in (2022, 2023, 2024, 2025):
    tr = TRAIN_OK & np.isfinite(x) & (YC < Yr); te = (YC == Yr) & ISM
    m, s = x[tr].mean(), x[tr].std(); z = np.clip((x - m) / s, -4, 4); yc = Y[tr] - Y[tr].mean(); beta = (z[tr] @ yc) / (z[tr] @ z[tr])
    WF[te] = np.nan_to_num(beta * z[te]) * SDD[te] * np.sqrt(DSESS[te]); print(f'  walk-forward beta for {Yr}: {beta:+.4f}')
S_WF = book_mask(ground(WF), TEST); S_B = book_mask(G0v, TEST)
out = []
for lab, yrs in (('2022', [2022]), ('2023', [2023]), ('2024', [2024]), ('2025', [2025]), ('2022-25', [2022, 2023, 2024, 2025])):
    M = np.isin(YC, yrs)
    for nm, S_ in (('canon', S_B), ('gap g=1', S_WF)):
        mm = metr(C[S_ & M], max(yrs)); out.append(dict(period=lab, book=nm, **mm))
print('\n=== walk-forward 2022-25, market gap gamma 1 vs canon ===')
print(pd.DataFrame(out).to_string(index=False))

# ── scale sensitivity (reported, not used to choose) ──
x = np.full(len(C), np.nan); x[hit] = MKALL['mkt_gap'].values[pp[hit]]
trA = TRAIN_OK & np.isfinite(x); mA, sA = x[trA].mean(), x[trA].std(); zA = np.clip((x - mA) / sA, -4, 4)
betaA = (zA[trA] @ (Y[trA] - Y[trA].mean())) / (zA[trA] @ zA[trA])
rows = []
for gm in (0.5, 1.0, 1.5, 2.0, 3.0):
    s_wf = book_mask(ground(gm * WF), TEST); m_wf = metr(C[s_wf], 2025)
    mu26 = np.where(OOTM, np.nan_to_num(gm * betaA * zA) * SDD * np.sqrt(DSESS), 0.0); s26 = book_mask(ground(mu26), OOTM); m26 = metr(C[s26], 2026)
    rows.append(dict(gamma=gm, wf_pnl=m_wf['pnl'], wf_sh=m_wf['sh'], wf_dd=m_wf['dd'], oot_pnl=m26['pnl'], oot_sh=m26['sh'], oot_dd=m26['dd']))
print('\n=== scale sensitivity: 2022-25 walk-forward (canon $20,135 Sh 1.34 DD -20.43) | 2026 frozen (canon $4,372 Sh 2.62 DD -4.24) ===')
print(pd.DataFrame(rows).to_string(index=False))
