import sys, warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); import ent_canon as ec
r = pd.read_parquet(sys.argv[1]); r['yr'] = r.entry_date.dt.year
b = r.model_credit / (r.width - r.model_credit)
P = r[['p','q','ro']].to_numpy(float); Q = r[['q_win','q_loss','q_part']].to_numpy(float)
_, g_mkt = ec.kelly(np.nan_to_num(Q[:,0]), np.nan_to_num(Q[:,1]), np.nan_to_num(Q[:,2]), b.to_numpy())
eps = 1e-9
def kl(A, B): return (A * np.log((A+eps)/(B+eps))).sum(1)
def H(A): return -(A*np.log(A+eps)).sum(1)
S = {
 'GROUND': r.GROUND, 'G (EV)': r.EV, 'D_ent': r.D_ent, 'D col': r.D,
 'G_mkt': np.exp(g_mkt)-1, 'G_mod - G_mkt': r.EV - (np.exp(g_mkt)-1),
 'p - q_win': r.p - r.q_win, 'q - q_loss': r.q - r.q_loss, 'p': r.p, 'q': r.q, 'ro': r.ro,
 'KL(P||Q)': kl(P,Q), 'KL(Q||P)': kl(Q,P), 'H(P)': H(P), 'ln3-H(P)': np.log(3)-H(P),
 'linEV_mod': r.p*r.model_credit - r.q*(r.width-r.model_credit) + r.ro*0,
 'credit/width': r.model_credit/r.width, 'b': b, 'short_delta': r.dfit_short.abs(), 'dist': r.dist, 'DTE': r.DTE,
 'width': r.width, 'entry_price': r.entry_price, 'fit_rmse': r.fit_rmse, 'cp_iv_gap': r.cp_iv_gap, 'parity_pct': r.parity_pct,
 'GROUND k=0': r.EV, 'GROUND k=8': r.EV*np.exp(-8*r.D_ent), 'GROUND k=16': r.EV*np.exp(-16*r.D_ent),
 'EV*exp(-4 KL(P||Q))': r.EV*np.exp(-4*kl(P,Q)), 'EV*exp(-4 (ln3-H(P)))': r.EV*np.exp(-4*(np.log(3)-H(P))),
}
def stats(x, y):
    x = pd.Series(np.asarray(x, float)); y = pd.Series(np.asarray(y, float)); ok = x.notna() & y.notna(); x, y = x[ok], y[ok]
    if len(x) < 200 or x.nunique() < 10: return np.nan, np.nan, 0, np.full(5, np.nan)
    rho = x.rank().corr(y.rank()); n = len(x)
    qq = pd.qcut(x.rank(method='first'), 5, labels=False); m = y.groupby(qq).mean()
    t = (m[4]-m[0]) / np.sqrt(y[qq==4].var()/(qq==4).sum() + y[qq==0].var()/(qq==0).sum())
    mono = int(np.all(np.diff(m.values) > 0)) - int(np.all(np.diff(m.values) < 0))
    return rho, t, mono, m.values.round(1)
for gate_name, gm in (('pool (no thr)', np.ones(len(r), bool)), ('GROUND>=thr', (r.GROUND >= ec.THR).to_numpy())):
    rows = []
    for k, v in S.items():
        for win, wm in (('IS', r.yr <= 2025), ('OOT', r.yr == 2026)):
            m = gm & wm.to_numpy()
            rho, t, mono, q5 = stats(np.asarray(v)[m], r.pnl_per_contract.to_numpy()[m])
            rows.append(dict(score=k, win=win, rho=round(rho,3), t_top_minus_bot=round(t,2), mono=mono, q5=' '.join(f'{x:6.1f}' for x in q5)))
    R = pd.DataFrame(rows).pivot(index='score', columns='win', values=['rho','t_top_minus_bot','mono','q5']).reindex(list(S))
    R.columns = [f'{a}_{b}' for a,b in R.columns]; R = R[['rho_IS','rho_OOT','t_top_minus_bot_IS','t_top_minus_bot_OOT','mono_IS','mono_OOT','q5_IS','q5_OOT']]
    pd.set_option('display.width', 250); print(f'\n=== {gate_name}: n IS {int((gm & (r.yr<=2025)).sum())}, OOT {int((gm & (r.yr==2026)).sum())} ==='); print(R.to_string())
