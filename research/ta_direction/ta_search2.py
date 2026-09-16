"""Second TA search, built to answer "does anything beat chance?" (user, 2026-09-16).

The first search (ta_search.py) screened ~140 veto rules and reported a lone survivor that failed at
its neighbouring threshold. This version fixes the three holes in that method:

  1. Every rule is scored on the BOOK WITH REFILL (vetoed slot -> next-ranked candidate), not on the
     vetoed trades in isolation. Statistic = paired t of weekly P&L (rule book minus canon book), IS 2020-25.
  2. Family-wise null: the whole feature matrix is shuffled across candidates WITHIN each entry date
     (keeps the per-day structure and feature correlations, destroys any link to outcome) and the full
     rule grid is re-scored. A rule is only real if its t beats the 95th percentile of the null MAX t.
  3. Plateau: the same feature/tail must improve the book at most of its six thresholds, and in >= 5 of 6 years.
2026 (OOT) is reported for rules that clear all three, never used to choose. It is not a pristine holdout (§0.43).

New features vs ta_search.py: correlation peer group moves (the §0.44 energy case, without a hand-made
sector map) and absolute sigma spike thresholds from §0.44 #2.

    python3 research/ta_direction/ta_search2.py [--perms 200]
"""
import sys, argparse
_ap = argparse.ArgumentParser(); _ap.add_argument('--perms', type=int, default=200); NPERM = _ap.parse_args().perms
sys.argv = ['ta_search.py', '--mode', 'is']
src = open('research/ta_direction/ta_search.py').read().split('# ── 4. screen')[0]
exec(compile(src, 'ta_search.py[1-3]', 'exec'))          # C, select, metr, feats, bars, log, DIRS, NONDIR, SR, ec, rmc

# ── peer-group features: 5 most correlated names on trailing 120d returns through t-1 ───────────────
PX = pd.concat({t: bars(t).set_index('date').close for t in tick}, axis=1).sort_index()
LR = np.log(PX).diff()
RZ = pd.concat({t: FE[FE.ticker == t].set_index('entry_date').ret1z for t in tick}, axis=1).reindex(PX.index)   # prior-session z
R5 = pd.concat({t: FE[FE.ticker == t].set_index('entry_date').ret5z for t in tick}, axis=1).reindex(PX.index)
dates = [d for d in sorted(C.entry_date.unique()) if d in PX.index]; rows = []
log(f'entry dates with no Yahoo bar (no peer value): {[str(d.date()) for d in sorted(C.entry_date.unique()) if d not in PX.index]}')
for i, d in enumerate(dates):
    w = LR.loc[:d].iloc[-121:-1]                                        # 120 sessions ending the prior session
    w = w.loc[:, w.notna().sum() >= 100]
    cm = w.corr().values; np.fill_diagonal(cm, -np.inf); cols = w.columns
    rz, r5 = RZ.loc[d, cols].values, R5.loc[d, cols].values
    top = np.argsort(-np.nan_to_num(cm, nan=-np.inf), axis=1)[:, :5]
    rows.append(pd.DataFrame({'ticker': cols, 'entry_date': d, 'peer_ret1z': np.nanmean(rz[top], axis=1),
                              'peer_ret5z': np.nanmean(r5[top], axis=1), 'peer_corr': np.take_along_axis(cm, top, 1).mean(1)}))
    if (i + 1) % 300 == 0 or i + 1 == len(dates): log(f'peer groups {i+1}/{len(dates)} dates ({100*(i+1)/len(dates):.0f}%)')
C = C.merge(pd.concat(rows), on=['ticker', 'entry_date'], how='left')
C['peer_ret1z_s'] = C.peer_ret1z * C.side; C['peer_ret5z_s'] = C.peer_ret5z * C.side
C['own_minus_peer1_s'] = (C.ret1z - C.peer_ret1z) * C.side
log(f'peer coverage {100*C.peer_ret1z.notna().mean():.1f}% of candidates')

# ── fast book with refill on the IS eligible pool ──────────────────────────────────────────────────
def pool(win):
    E = C[(C.win == win) & (C.GROUND >= ec.THR)].sort_values(['entry_date', 'GROUND'], ascending=[True, False]).reset_index(drop=True)
    E['dcode'] = E.entry_date.factorize()[0]; E['wk'] = E.entry_date.dt.to_period('W').factorize(sort=True)[0]; E['yr'] = E.entry_date.dt.year
    return E
def starts(E): return np.flatnonzero(np.r_[1, np.diff(E.dcode.values)])
def book(E, veto):
    keep = ~veto; cs = np.cumsum(keep); st = starts(E)
    before = np.r_[0, cs][st]                                            # kept count before each day's first row
    rank = cs - np.repeat(before, np.diff(np.r_[st, len(E)]))            # 1-based rank among kept rows that day
    return keep & (rank <= ec.TOP_N)
IS = pool('IS'); OOT = pool('OOT')
PNL, WK, NW, YR = IS.pnl_per_contract.values, IS.wk.values, IS.wk.max() + 1, IS.yr.values
base_sel = book(IS, np.zeros(len(IS), bool)); base_w = np.bincount(WK, PNL * base_sel, NW)
assert base_sel.sum() == len(select(C[C.win == 'IS'])), 'fast book does not reproduce select()'
log(f'fast book reproduces canon IS: {base_sel.sum()} trades ${PNL[base_sel].sum():,.0f}')

FEATS = [f + '_s' for f in DIRS] + ['peer_ret1z_s', 'peer_ret5z_s', 'own_minus_peer1_s'] + SR
SIDED = NONDIR + ['peer_corr']
QS = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30)
def grid(E):
    """(name, feat, side, tail, q, mask) for every rule, thresholds from E itself."""
    out = []
    for f in FEATS + [(g, s) for g in SIDED for s in ('bull_put', 'bear_call')]:
        feat, side = (f, None) if isinstance(f, str) else f
        x = E[feat].values; m_side = np.ones(len(E), bool) if side is None else (E.spread_type.values == side)
        ok = np.isfinite(x) & m_side
        if ok.sum() < 500: continue
        if feat in ('nr7', 'inside'):                                     # binary flags: one rule each way, not six copies
            for v, tail in ((1.0, 'hi'), (0.0, 'lo')):
                out.append((f'{feat}{"" if side is None else " "+side} = {v:g}', feat, side, tail, None, v, ok & (x == v)))
            continue
        for tail in ('lo', 'hi'):
            for q in QS:
                thr = np.quantile(x[ok], q if tail == 'lo' else 1 - q)
                out.append((f'{feat}{"" if side is None else " "+side} {tail} {int(q*100)}%', feat, side, tail, q, thr,
                            ok & ((x <= thr) if tail == 'lo' else (x >= thr))))
    for k in (1.0, 1.5, 2.0, 2.5):                                          # §0.44 #2: absolute sigma spike in your favour
        for feat in ('ret1z_s', 'peer_ret1z_s'):
            x = E[feat].values; out.append((f'{feat} >= {k} sigma', feat, None, 'abs', k, k, np.isfinite(x) & (x >= k)))
    return out
def tstat(sel):
    d = np.bincount(WK, PNL * sel, NW) - base_w
    return d.mean() / (d.std(ddof=1) / np.sqrt(NW)) if d.std() > 0 else 0.0

G0 = grid(IS)
res = []
for name, feat, side, tail, q, thr, vm in G0:
    sel = book(IS, vm)
    yrs = [PNL[sel & (YR == y)].sum() - PNL[base_sel & (YR == y)].sum() for y in range(2020, 2026)]
    res.append(dict(rule=name, feat=feat, side=side or 'all', tail=tail, q=q, thr=thr, n_veto=int(vm.sum()), t=tstat(sel),
                    dPnL=PNL[sel].sum() - PNL[base_sel].sum(), yrs_up=sum(v > 0 for v in yrs), yrs=[round(v) for v in yrs]))
R = pd.DataFrame(res)
R['plateau'] = R.groupby(['feat', 'side', 'tail']).dPnL.transform(lambda s: (s > 0).sum())
log(f'scored {len(R)} rules on IS')

# ── permutation null: shuffle feature rows within entry date, re-score the whole grid, keep max t ──────
rng = np.random.default_rng(20260916); FCOLS = sorted({g[1] for g in G0} | {'spread_type'})
maxt = []
for p in range(NPERM):
    perm = np.arange(len(IS)); st = starts(IS)
    for a, b in zip(st, np.r_[st[1:], len(IS)]): perm[a:b] = a + rng.permutation(b - a)
    Ep = IS.copy(); cols = [c for c in FCOLS if c != 'spread_type']
    Ep[cols] = IS[cols].values[perm]                                     # whole feature row moves together; outcome and side stay put
    maxt.append(max(tstat(book(IS, vm)) for *_, vm in grid(Ep)))
    if (p + 1) % 20 == 0 or p + 1 == NPERM: log(f'null permutations {p+1}/{NPERM} ({100*(p+1)/NPERM:.0f}%)')
maxt = np.array(maxt); T95 = np.quantile(maxt, 0.95)
R['p_fwer'] = [(maxt >= t).mean() for t in R.t]
pd.set_option('display.max_rows', 60)
print(f'\n=== NULL: max t over {len(R)} rules, {NPERM} within-date shuffles: median {np.median(maxt):.2f}, 95th pct {T95:.2f}, max {maxt.max():.2f} ===')
print('\n=== TOP 25 IS rules by t (book with refill, weekly paired t vs canon) ===')
print(R.sort_values('t', ascending=False).head(25)[['rule', 'n_veto', 'dPnL', 't', 'p_fwer', 'yrs_up', 'plateau', 'yrs']].round(3).to_string(index=False))

# ── rules that clear all three bars, then 2026 for reference ───────────────────────────────────────
W = R[(R.t >= T95) & (R.yrs_up >= 5) & ((R.plateau >= 4) | (R.tail == 'abs'))]
print(f'\n=== PASS (t >= null 95th {T95:.2f}, >= 5/6 years, plateau >= 4/6): {len(W)} rules ===')
if len(W):
    def vmask(E, r):
        x = E[r.feat].values; vm = np.isfinite(x) & ((x <= r.thr) if r.tail == 'lo' else (x >= r.thr))
        return vm & (E.spread_type.values == r.side) if r.side != 'all' else vm
    out = []
    for _, r in W.iterrows():
        mi = metr(IS[book(IS, vmask(IS, r))], 2025)
        out.append(dict(rule=r.rule, IS_n=mi['n'], IS_pnl=mi['pnl'], IS_win=mi['win'], IS_sh=mi['sh'], IS_dd=mi['dd']))
    print(pd.DataFrame(out).to_string(index=False))
    # 2026 NOT evaluated: data/daily_bars_yahoo has no bars 2026-01-01..2026-05-14, so every rolling/shifted feature
    # in 2026 is NaN or spans the gap. A 2026 check needs complete 2026 OHLC first.
    print('canon IS:', metr(IS[base_sel], 2025))
else:
    print('Nothing beats the shuffled-feature null. No TA veto in this family is distinguishable from chance on 2020-25.')
R.drop(columns='yrs').to_csv('research/ta_direction/ta_search2_rules.csv', index=False)
pd.Series(maxt, name='null_max_t').to_csv('research/ta_direction/ta_search2_null.csv', index=False)
log('done')
