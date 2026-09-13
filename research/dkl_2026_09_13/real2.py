"""Real-vs-real: D(P_emp || P_real) and D(P_real || P_emp).  P_emp = 52:10 realized outcomes of the name's
past 20-delta spreads (usual placement); P_real = the name's raw DTE-matched moves vs TODAY's strikes.
Signed 'tight': fires when today's strikes are riskier than the usual placement (P_real riskier than P_emp)."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, select, kl, kelly_ell
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
C = pd.read_parquet(f'{HERE}/featR.parquet')
Pe = C[['p', 'q', 'ro']].values; Pr = C[['p_r', 'q_r', 'ro_r']].values
oke = np.isfinite(Pe).all(1) & (C.n_yr >= 120).values
Pe_ = np.where(oke[:, None], np.nan_to_num(Pe, nan=1/3), 1/3)
C['D_er_u'] = np.where(oke, kl(Pe_, Pr), 0.0); C['D_re_u'] = np.where(oke, kl(Pr, Pe_), 0.0)
tight = (C.ls_r > C.pred_loss_share.fillna(0)).values & oke
C['D_er'] = np.where(tight, C.D_er_u, 0.0); C['D_re'] = np.where(tight, C.D_re_u, 0.0)
C['ls_gap'] = np.where(oke, C.ls_r - C.pred_loss_share.fillna(0), 0.0)           # plain signed gap for the diagnostic
# mixed real belief
Pm = 0.5 * (Pe_ + Pr); b = C.net_credit.values / C.max_loss.values
C['ell_m'] = np.where(oke, kelly_ell(Pm[:, 0], Pm[:, 1], Pm[:, 2], b), np.nan); C['EV_m'] = np.exp(C.ell_m) - 1
C['Z'] = 0.0
print(f'tight (today\'s strikes riskier than usual placement) on {tight.mean():.1%} of candidates; med D_er nonzero {C.D_er[C.D_er>0].median():.4f}')
ISc, OOc = C[C.win == 'IS'], C[C.win == 'OOT']
def qt(df, col, score, pred):
    d = df.dropna(subset=[col, score]); d = d[d[col] > 0] if (d[col] > 0).mean() < 0.9 else d
    q = pd.qcut(d[col].rank(method='first'), 5, labels=False)
    g = d.groupby(q).agg(loss=('is_loss', 'mean'), real=('loss_share', 'mean'), EV=(score, 'mean')); g['excess'] = g.real - d.groupby(q)[pred].mean()
    return f"loss {g.loss.round(3).tolist()} excess-vs-belief {g.excess.round(3).tolist()} EV {g.EV.round(4).tolist()}"
def sw(dc, score, ks, fill, thr):
    de.FILL, de.THR = fill, thr; rows = []
    for k in ks:
        a = book(select(ISc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2025-12-31')); o = book(select(OOc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2026-08-31'))
        rows.append(dict(k=k, is_n=a['n'], is_fin=a['final'], is_sh=a['sharpe'], is_dd=a['dd'], is_loss=a['loss'], oot_n=o['n'], oot_fin=o['final'], oot_sh=o['sharpe'], oot_dd=o['dd'], oot_loss=o['loss']))
    return pd.DataFrame(rows)
for score, pred, lab in (('EV_r', 'ls_r', 'G on P_real'), ('EV', 'pred_loss_share', 'G on P_emp (52:10)'), ('EV_m', 'ls_r', 'G on mixed (P_emp+P_real)/2')):
    print(f'\n################ {lab}, fill 1.1x model, thr 0.02 ################')
    print('k=0:'); print(sw('Z', score, (0,), 1.1, 0.02).to_string(index=False, float_format=fmt))
    for dc in ('D_er', 'D_re', 'D_er_u', 'ls_gap'):
        if dc == 'ls_gap':
            C['ls_gap_p'] = np.clip(C.ls_gap, 0, None); ISc, OOc = C[C.win == 'IS'], C[C.win == 'OOT']; dc = 'ls_gap_p'
        print(f'\n=== DKL = {dc} ===  IS : {qt(ISc, dc, score, pred)}\n                      OOT: {qt(OOc, dc, score, pred)}')
        print(sw(dc, score, (0, 2, 4, 8, 16, 32, 64), 1.1, 0.02).to_string(index=False, float_format=fmt))
print('\n################ G on P_real, fill 1.0x, thr 0.02, DKL = D_er ################')
print(sw('D_er', 'EV_r', (0, 2, 4, 8, 16, 32), 1.0, 0.02).to_string(index=False, float_format=fmt))
C.to_parquet(f'{HERE}/featR2.parquet')
