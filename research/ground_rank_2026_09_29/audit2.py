import sys, warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); import ent_canon as ec
r = pd.read_parquet(sys.argv[1]); r['yr'] = r.entry_date.dt.year
b = r.model_credit / (r.width - r.model_credit)
Q = r[['q_win','q_loss','q_part']].to_numpy(float)
_, g_mkt = ec.kelly(np.nan_to_num(Q[:,0]), np.nan_to_num(Q[:,1]), np.nan_to_num(Q[:,2]), b.to_numpy())
S = {'GROUND': r.GROUND, 'G': r.EV, 'D_ent': r.D_ent, 'G_mkt': np.exp(g_mkt)-1, 'G_mod-G_mkt': r.EV-(np.exp(g_mkt)-1), 'q': r.q, 'p': r.p, 'b': b, 'q-q_loss': r.q-r.q_loss}
Y = {'ror': r.pnl_per_contract/(100*r.max_loss_adj), 'win': (r._outcome=='WIN').astype(float), 'notloss': (r._outcome!='LOSS').astype(float)}
print('ror sd', round(Y['ror'].std(),3), ' win sd', round(Y['win'].std(),3))
def stats(x, y):
    x = pd.Series(np.asarray(x, float)); y = pd.Series(np.asarray(y, float)); ok = x.notna() & y.notna(); x, y = x[ok], y[ok]
    rho = x.rank().corr(y.rank()); qq = pd.qcut(x.rank(method='first'), 5, labels=False); m = y.groupby(qq).mean()
    t = (m[4]-m[0]) / np.sqrt(y[qq==4].var()/(qq==4).sum() + y[qq==0].var()/(qq==0).sum())
    return rho, t, m.values
rows = []
for k, v in S.items():
    v = pd.Series(np.asarray(v, float), index=r.index)
    for form, x in (('global', v), ('within-day pct', v.groupby(r.entry_date).rank(pct=True))):
        for yn, y in Y.items():
            for win, wm in (('IS', r.yr <= 2025), ('OOT', r.yr == 2026)):
                rho, t, q5 = stats(x[wm], y[wm])
                rows.append(dict(score=k, form=form, target=yn, win=win, rho=round(rho,3), t=round(t,2), q5=' '.join(f'{100*z:5.1f}' for z in q5)))
R = pd.DataFrame(rows); pd.set_option('display.width', 250); pd.set_option('display.max_rows', 500)
P = R.pivot_table(index=['score','form','target'], columns='win', values=['rho','t','q5'], aggfunc='first', sort=False)
P.columns = [f'{a}_{b}' for a,b in P.columns]; print(P[['rho_IS','rho_OOT','t_IS','t_OOT','q5_IS','q5_OOT']].to_string())
