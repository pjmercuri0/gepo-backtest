"""Market-level TA forecasts feeding P_real (user, 2026-09-16).

ta_predict.py / the per-year IC table showed stock-level TA has no cross-sectional power (within a day, the
gap rank says nothing about which name does better), but the AVERAGE opening gap across names predicts the
average move to expiry at the date level. So the forecasts here are market-wide: one drift per date, from
breadth averages of each TA feature over all 83 names plus SPY's own gap, fitted walk-forward on earlier IS
years (test 2022-25), pushed into P_real as a shift of every historical move (scaled to each name's sigma).

Null: the market feature matrix is circularly shifted by a random 60+ session offset (keeps each feature's
autocorrelation, breaks its alignment with outcomes) and every variant is refitted and rescored.

    python3 research/ta_direction/ta_predict_mkt.py [--perms 200]
"""
import sys, argparse
_ap = argparse.ArgumentParser(); _ap.add_argument('--perms', type=int, default=200); NPM = _ap.parse_args().perms
sys.argv = ['ta_predict.py', '--perms', '0']
exec(compile(open('research/ta_direction/ta_predict.py').read().split('GAMMAS = ')[0], 'ta_predict.py[1-4]', 'exec'))

# ── market (breadth) features per date: mean over all names' prior-session TA, plus SPY gap ────────────
EXA = pd.concat(EX).rename(columns={'date': 'entry_date'})
ALLF = FE.merge(EXA, on=['ticker', 'entry_date'], how='left')
MK_FEATS = [f for f in DIRS + NEW_DIR if f in ALLF.columns and not f.startswith('spy_')]
MK = ALLF.groupby('entry_date')[MK_FEATS].mean().add_prefix('mkt_')
sp = bars('SPY'); sdv = np.log(sp.close).diff().rolling(20).std().shift()
MK = MK.join(pd.DataFrame({'mkt_spy_gapz': ((sp.open / sp.close.shift() - 1) / sdv).values,
                           'mkt_spy_ret1z': ((sp.close / sp.close.shift() - 1) / np.log(sp.close).diff().rolling(20).std()).shift().values,
                           'mkt_spy_dsma20': (sp.close / sp.close.rolling(20).mean() - 1).shift().values}, index=sp.date), how='left')
MK = MK.sort_index(); MCOLS = list(MK.columns)
DATES = MK.index.values
cpos = np.searchsorted(DATES, C.entry_date.values.astype('datetime64[ns]')); cpos_ok = (cpos < len(DATES)) & (DATES[np.clip(cpos, 0, len(DATES) - 1)] == C.entry_date.values.astype('datetime64[ns]'))
log(f'{len(MCOLS)} market features; candidates matched to a market date: {100*cpos_ok.mean():.1f}% (2026 gap excluded anyway)')
Y = np.clip(C.fwd_z.values, -5, 5); TRAIN_OK = STOCKDAY & ISM & np.isfinite(Y) & cpos_ok
def forecasts(MKv):
    """MKv: market feature matrix aligned to DATES. Walk-forward univariate + ridge z forecasts per candidate."""
    X = np.full((len(C), MKv.shape[1]), np.nan); X[cpos_ok] = MKv[cpos[cpos_ok]]
    X = np.nan_to_num(np.clip(X, -50, 50)); out = {f: np.full(len(C), np.nan) for f in MCOLS + ['mkt_ridge']}
    for Yr in (2022, 2023, 2024, 2025):
        tr = TRAIN_OK & (YC < Yr); te = (YC == Yr) & cpos_ok
        m, s = X[tr].mean(0), X[tr].std(0); s[s == 0] = 1; Z = np.clip((X - m) / s, -4, 4); yc = Y[tr] - Y[tr].mean()
        for j, f in enumerate(MCOLS):
            b_ = (Z[tr, j] @ yc) / max(Z[tr, j] @ Z[tr, j], 1e-9); out[f][te] = b_ * Z[te, j]
        w = np.linalg.solve(Z[tr].T @ Z[tr] + 2000.0 * np.eye(Z.shape[1]), Z[tr].T @ yc); out['mkt_ridge'][te] = Z[te] @ w
    return out
GAMMAS = (1.0, 3.0, 10.0)
def run_mkt(MKv):
    res = {}
    for f, z in forecasts(MKv).items():
        for gm in GAMMAS:
            res[(f, gm)] = book_g(ground(np.nan_to_num(gm * z) * SDD * np.sqrt(DSESS)))
    return res
MKV = MK.values
RES = run_mkt(MKV)
TR = pd.DataFrame([dict(model=k[0], gamma=k[1], **evaluate(v)) for k, v in RES.items()])
log(f'scored {len(TR)} market-forecast variants on 2022-25')

rng = np.random.default_rng(20260917); NUL = []
for p_ in range(NPM):
    off = rng.integers(60, len(DATES) - 60)
    NUL.append(max(evaluate(v)['t'] for v in run_mkt(np.roll(MKV, off, axis=0)).values()))
    if (p_ + 1) % 20 == 0 or p_ + 1 == NPM: log(f'null {p_+1}/{NPM} ({100*(p_+1)/NPM:.0f}%)')
NUL = np.array(NUL) if NPM else np.array([np.nan])
TR['p_fwer'] = [(NUL >= t).mean() for t in TR.t]
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 100)
print(f'\n=== market TA forecast -> P_real, test 2022-25 (canon {int((BASE & TEST).sum())} trades ${PN[BASE & TEST].sum():,.0f}); '
      f'{len(TR)} variants; null (date shift) max t median {np.nanmedian(NUL):.2f}, 95th {np.nanquantile(NUL, .95):.2f} ===')
print(TR.sort_values('t', ascending=False).head(25).to_string(index=False))
TR.drop(columns='yrs').to_csv('research/ta_direction/ta_predict_mkt_results.csv', index=False)
pd.Series(NUL, name='null_max_t').to_csv('research/ta_direction/ta_predict_mkt_null.csv', index=False)
log('done')
