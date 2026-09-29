import sys, warnings, time; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); sys.path.insert(0, 'research')
import ent_canon as ec
from bear_regime_sweep import GAP_SERIES
S = sys.argv[1]
r = pd.read_parquet(f'{S}/bull_pool.parquet'); r['yr'] = r.entry_date.dt.year
closes = ec.backtest_closes(); gaps = pd.read_parquet(GAP_SERIES)
b = (r.model_credit / (r.width - r.model_credit)).to_numpy()
ror = (r.pnl_per_contract / (100 * r.max_loss_adj)).to_numpy()
def stats(x, y):
    x = pd.Series(np.asarray(x, float)); y = pd.Series(np.asarray(y, float)); ok = x.notna() & y.notna(); x, y = x[ok], y[ok]
    if len(x) < 200: return np.nan, np.nan, np.full(5, np.nan)
    rho = x.rank().corr(y.rank()); qq = pd.qcut(x.rank(method='first'), 5, labels=False); m = y.groupby(qq).mean()
    t = (m[4]-m[0]) / np.sqrt(y[qq==4].var()/(qq==4).sum() + y[qq==0].var()/(qq==0).sum())
    return rho, t, m.values
rows = []
for window, demean, gap in [(252, True, True), (63, True, True), (126, True, True), (504, True, True), (252, False, True), (252, True, False), (252, False, False), (126, False, True), (504, False, True)]:
    t0 = time.time(); ec.P_REAL_DEMEAN = demean
    mu = ec.gap_drift(r, closes, gaps) if gap else None
    P = ec.p_real(r, closes, window=window, mu=mu)
    _, ell = ec.kelly(np.nan_to_num(P[:,0]), np.nan_to_num(P[:,1]), np.nan_to_num(P[:,2]), b); ell[~np.isfinite(P[:,0])] = np.nan
    EV = np.exp(ell) - 1; GR = EV * np.exp(-ec.K * r.D_ent.to_numpy())
    for sname, sv in (('GROUND', GR), ('G', EV), ('p', P[:,0]), ('q', P[:,1])):
        for win, wm in (('IS', (r.yr <= 2025).to_numpy()), ('OOT', (r.yr == 2026).to_numpy())):
            rho, t, q5 = stats(sv[wm], ror[wm])
            rows.append(dict(window=window, demean=demean, gap=gap, score=sname, win=win, n=int(wm.sum()), rho=round(rho,3), t=round(t,2), q5=' '.join(f'{100*x:5.1f}' for x in q5)))
    print(window, demean, gap, f'{time.time()-t0:.0f}s', flush=True)
    pd.DataFrame(rows).to_csv(f'{S}/preal_variants.csv', index=False)
R = pd.DataFrame(rows); pd.set_option('display.width', 250); print(R.to_string(index=False))
