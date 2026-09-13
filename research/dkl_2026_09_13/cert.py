import sys, os, time
import numpy as np, pandas as pd
SCR = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, SCR)
import dkl_eval as de
from dkl_eval import *
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
IS = pd.read_parquet(f'{SCR}/feat2_IS.parquet'); OO = pd.read_parquet(f'{SCR}/feat2_OOT.parquet')
for C in (IS, OO):
    p_iv = 1.0 - C.ls_iv * 0 - (C.ls_iv * 0)  # placeholder overwritten below
    # rebuild p_iv, p_d from stored loss shares is lossy; recompute directly
    from diag import iv_triple
    Qiv = iv_triple(C, C.IV.values, C.long_IV.fillna(C.IV).values)
    C['p_iv'] = Qiv[:, 0]
    C['D_cert_iv'] = -np.log(np.clip(C.p_iv, 1e-6, 1))           # D(Q_cert || Q_iv): market's surprise at a WIN
    C['D_cert_d'] = -np.log(np.clip(1 - C.d_sh, 1e-6, 1))          # same with delta
    C['D_cert_emp'] = -np.log(np.clip(C.p, 1e-6, 1))               # Opus #3 (should fail: p_emp is in G)
    C['D_sum'] = C.D_emp_iv + C.D_cert_iv
KS = (0, 1, 2, 3, 4, 6, 8, 10, 12, 16, 24)
for dc in ['D_cert_iv', 'D_cert_d', 'D_cert_emp', 'D_sum']:
    print(f'\n===== {dc} (thr 0.05, G on ticker cell) =====')
    print('IS :', quintile_table(IS, dc)); print('OOT:', quintile_table(OO, dc))
    print(sweep(IS, OO, dc, ks=KS).to_string(index=False, float_format=fmt))
print('\n===== D_cert_iv on shrunk-G base (EV_post) =====')
print(sweep(IS, OO, 'D_cert_iv', score='EV_post', ks=KS).to_string(index=False, float_format=fmt))
# top-20/day loss by D_cert_iv quintile
for C, lab in ((IS, 'IS'), (OO, 'OOT')):
    d = C.dropna(subset=['EV']); top = d.sort_values(['entry_date', 'EV'], ascending=[True, False]).groupby('entry_date').head(20)
    q = pd.qcut(top.D_cert_iv.rank(method='first'), 5, labels=False)
    print(f'{lab} top-20/day loss by D_cert_iv quintile:', top.groupby(q).is_loss.mean().round(4).tolist(),
          ' EV:', top.groupby(q).EV.mean().round(4).tolist())
IS.to_parquet(f'{SCR}/feat3_IS.parquet'); OO.to_parquet(f'{SCR}/feat3_OOT.parquet')
