"""TA as a PREDICTION feeding P_real, not a filter (user, 2026-09-16).

The canon's P_real is the empirical distribution of the name's last 252 DTE-session moves, read against the
exact strikes. Here a TA model forecasts the stock's drift over the trade's life, mu, and every historical move
is shifted by mu before it is counted. p/q/ro, Kelly EV, GROUND, the threshold, the side chosen and the top-5
all follow from that, so the forecast can ADD trades, REMOVE trades and SWITCH sides.

Forecasts are walk-forward: the model for year Y is fitted only on candidates entered before Y (IS window).
Test years 2022-2025. 2026 is excluded (Yahoo bars missing 2026-01-01..05-14).

    python3 research/ta_direction/ta_predict.py [--perms 100]
"""
import sys, argparse
_ap = argparse.ArgumentParser(); _ap.add_argument('--perms', type=int, default=100); NPP = _ap.parse_args().perms
sys.argv = ['ta_search3.py', '--perms', '0']
exec(compile(open('research/ta_direction/ta_search3.py').read().split('# ── B. single-feature')[0], 'ta_search3.py[1-2]', 'exec'))

# ── vectorised P_real with a drift shift ─────────────────────────────────────────────────────────────
C = C.reset_index(drop=True)
px = CL.dropna().drop_duplicates(['ticker', 'date']).sort_values(['ticker', 'date'])
RM = np.full((len(C), ec.WINDOW), np.nan, dtype=np.float64)
for tk, g in C.groupby('ticker'):
    s = px[px.ticker == tk]
    if len(s) < 60: continue
    dates = pd.to_datetime(s.date).values.astype('datetime64[ns]'); cl = s.close.values.astype(float)
    pos_ = np.clip(np.searchsorted(dates, g.entry_date.values.astype('datetime64[ns]')), 0, len(cl) - 1)
    for d in (1, 2, 3, 4):
        m = (g.DTE.clip(1, 4).astype(int) == d).values
        if not m.any(): continue
        R_ = cl[d:] / cl[:-d] - 1.0
        idx = (pos_[m] - d - 1)[:, None] - np.arange(ec.WINDOW)[None, :]; ok = idx >= 0
        RM[g.index.values[m]] = np.where(ok, R_[np.clip(idx, 0, len(R_) - 1)], np.nan)
BP = (C.spread_type == 'bull_put').values
THS = (C.short_strike / C.entry_price - 1).values; THL = (C.long_strike / C.entry_price - 1).values
BCR = (C.model_credit / (C.width - C.model_credit)).values
def triple(mu):
    r = RM + mu[:, None]; ok = ~np.isnan(RM)
    bs = np.where(BP[:, None], r <= THS[:, None], r >= THS[:, None]) & ok
    bl = np.where(BP[:, None], r <= THL[:, None], r >= THL[:, None]) & ok
    n, ns, nl = ok.sum(1), bs.sum(1), bl.sum(1)
    t = np.column_stack([n - ns + ec.PRIOR, nl + ec.PRIOR, ns - nl + ec.PRIOR]).astype(float); return t / t.sum(1, keepdims=True)
def ground(mu):
    t = triple(np.nan_to_num(mu))                                        # a missing forecast is no forecast, never NaN
    _, ell = ec.kelly(t[:, 0], t[:, 1], t[:, 2], BCR)
    return (np.exp(ell) - 1) * np.exp(-ec.K * C.D_ent.values)
G0v = ground(np.zeros(len(C)))
err = np.nanmax(np.abs(triple(np.zeros(len(C))) - C[['p', 'q', 'ro']].values))
log(f'vectorised P_real vs canon p_real: max abs diff {err:.2e}')
assert np.abs(triple(np.zeros(len(C))) - C[['p', 'q', 'ro']].values).max() < 1e-12
log('vectorised P_real reproduces canon exactly')

# ── books on the full candidate pool (threshold + top-5 re-applied after the forecast) ────────────────
YC = C.entry_date.dt.year.values; ISM = (C.win == 'IS').values
TEST = np.isin(YC, (2022, 2023, 2024, 2025)) & ISM
dc = C.entry_date.factorize()[0]; wkc = C.entry_date.dt.to_period('W').factorize(sort=True)[0]; NWC = wkc.max() + 1
PN = C.pnl_per_contract.values
def book_g(G):
    key = np.where(np.isfinite(G) & (G >= ec.THR) & ISM, G, -np.inf)
    o = np.lexsort((-key, dc)); d = dc[o]; st = np.flatnonzero(np.r_[1, np.diff(d)])
    rank = np.arange(len(o)) - np.repeat(st, np.diff(np.r_[st, len(o)])) + 1
    sel = np.zeros(len(C), bool); sel[o] = (rank <= ec.TOP_N) & np.isfinite(key[o]); return sel
BASE = book_g(G0v)
assert BASE.sum() == 4554 and abs(PN[BASE].sum() - 30842) < 1
BW = np.bincount(wkc, PN * (BASE & TEST), NWC)
TW = np.unique(wkc[TEST])
def evaluate(sel):
    d = (np.bincount(wkc, PN * (sel & TEST), NWC) - BW)[TW]
    t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if d.std() > 0 else 0.0
    yrs = [round(PN[sel & TEST & (YC == y)].sum() - PN[BASE & TEST & (YC == y)].sum()) for y in (2022, 2023, 2024, 2025)]
    return dict(dPnL=sum(yrs), t=round(t, 2), yrs_up=sum(v > 0 for v in yrs), n=int((sel & TEST).sum()), n_base=int((BASE & TEST).sum()),
                side_switch=int(((sel & TEST) & ~BASE).sum()), yrs=yrs)

# ── forecast target and features ────────────────────────────────────────────────────────────────────
DSESS = C.DTE.clip(1, 4).values
# daily sigma from the close store (complete through 2026, unlike the Yahoo bars): 20 sessions ending the prior session
_px = px.copy(); _px['sd'] = _px.groupby('ticker').close.transform(lambda c: np.log(c).diff().rolling(20).std().shift())
SDD = C[['ticker', 'entry_date']].merge(_px.rename(columns={'date': 'entry_date'})[['ticker', 'entry_date', 'sd']], on=['ticker', 'entry_date'], how='left').sd.values
log(f'daily sigma available for {100*np.isfinite(SDD).mean():.1f}% of candidates')
C['fwd_z'] = np.log(C.expiry_close / C.entry_price) / (SDD * np.sqrt(DSESS))
RAW_DIR = DIRS + ['peer_ret1z', 'peer_ret5z'] + NEW_DIR
C['own_minus_peer1'] = C.ret1z - C.peer_ret1z; RAW_DIR = RAW_DIR + ['own_minus_peer1']
STOCKDAY = ~C.duplicated(['ticker', 'entry_date', 'DTE']).values          # one training row per stock/day/horizon
def fit_forecasts(F):
    """Walk-forward z forecasts per candidate for 2022-25 from models fitted on earlier IS years.
    Returns dict name -> zhat (NaN outside test years), in units of the name's DTE-session sigma."""
    out = {}
    X = np.clip(np.nan_to_num(F[RAW_DIR].values.astype(float)), -50, 50); y = np.clip(C.fwd_z.values, -5, 5)
    okr = STOCKDAY & ISM & np.isfinite(y)
    zr = {'ridge_all': np.full(len(C), np.nan)}; zu = {f: np.full(len(C), np.nan) for f in RAW_DIR}
    for Y in (2022, 2023, 2024, 2025):
        tr = okr & (YC < Y); te = YC == Y
        m, s = X[tr].mean(0), X[tr].std(0); s[s == 0] = 1; Z = (X - m) / s; Z = np.clip(Z, -4, 4)
        w = np.linalg.solve(Z[tr].T @ Z[tr] + 200.0 * np.eye(Z.shape[1]), Z[tr].T @ (y[tr] - y[tr].mean()))
        zr['ridge_all'][te] = Z[te] @ w + y[tr].mean()
        for j, f in enumerate(RAW_DIR):
            zj = Z[tr, j]; b_ = (zj @ (y[tr] - y[tr].mean())) / max(zj @ zj, 1e-9); zu[f][te] = b_ * Z[te, j]
    out.update(zr); out.update({'uni_' + f: v for f, v in zu.items()})
    return out
GAMMAS = (1.0, 3.0, 10.0)
def run_all(F):
    res = {}
    for name, zhat in fit_forecasts(F).items():
        for gm in GAMMAS:
            mu = np.nan_to_num(gm * zhat) * SDD * np.sqrt(DSESS); mu = np.nan_to_num(mu)
            res[(name, gm)] = book_g(ground(mu))
    return res
RES = run_all(C)
rows = [dict(model=k[0], gamma=k[1], **evaluate(v)) for k, v in RES.items()]
TR = pd.DataFrame(rows); log(f'scored {len(TR)} forecast variants on 2022-25')
FZ = fit_forecasts(C)
for nm in ('ridge_all',):
    z = FZ[nm][TEST & STOCKDAY]; yy = C.fwd_z.values[TEST & STOCKDAY]; okk = np.isfinite(z) & np.isfinite(yy)
    log(f'{nm}: out-of-sample corr(forecast, realised fwd z) 2022-25 = {np.corrcoef(z[okk], yy[okk])[0,1]:+.4f}, forecast sd {z[okk].std():.3f} sigma')

# ── null: shuffle feature rows across tickers within date, refit everything ──────────────────────────
rng = np.random.default_rng(9162); NULLT = []
order = np.argsort(dc, kind='stable'); st0 = np.flatnonzero(np.r_[1, np.diff(dc[order])])
for p_ in range(NPP):
    perm = order.copy()
    for a, b in zip(st0, np.r_[st0[1:], len(C)]): perm[a:b] = order[a:b][rng.permutation(b - a)]
    sh = np.empty(len(C), int); sh[order] = perm
    F = C[RAW_DIR].iloc[sh].reset_index(drop=True)
    NULLT.append(max(evaluate(v)['t'] for v in run_all(F).values()))
    if (p_ + 1) % 10 == 0 or p_ + 1 == NPP: log(f'null {p_+1}/{NPP} ({100*(p_+1)/NPP:.0f}%)')
NULLT = np.array(NULLT) if NPP else np.array([np.nan])
TR['p_fwer'] = [(NULLT >= t).mean() for t in TR.t]
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 80)
print(f'\n=== TA forecast -> P_real, test years 2022-25 (canon {int((BASE & TEST).sum())} trades ${PN[BASE & TEST].sum():,.0f}). '
      f'{len(TR)} variants; null max t median {np.nanmedian(NULLT):.2f}, 95th {np.nanquantile(NULLT, .95):.2f} ===')
print(TR.sort_values('t', ascending=False).head(30).to_string(index=False))
TR.drop(columns='yrs').to_csv('research/ta_direction/ta_predict_results.csv', index=False)
log('done')
