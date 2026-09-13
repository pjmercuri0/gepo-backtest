"""DKL sweep on SYNTHETIC credit: b and P&L at the smile-fit model credit, Q_iv from fitted IVs.
Vendor quotes are used only to fit the smile. Usage: python3 eval_synth.py <is_synth.parquet|none> <oot_synth.parquet>"""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import *
from diag import add_market
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
P, ex = load_pop()

def prep(path, tag):
    C = pd.read_parquet(path)
    C['vendor_mid'] = C.net_credit; C['vendor_IV'] = C.IV
    C['net_credit'] = C.model_credit; C['max_loss'] = (C.width - C.model_credit).round(4)
    C['IV'] = C.iv_fit_short; C['long_IV'] = C.iv_fit_long
    n0 = len(C); C = C[(C.net_credit > 0.01) & (C.max_loss > 0)].copy()
    print(f'[{tag}] {n0:,} -> {len(C):,} with positive model credit; med model {C.net_credit.median():.3f} vendor mid {C.vendor_mid.median():.3f} '
          f'model/vendor med {(C.net_credit/C.vendor_mid).median():.3f}')
    C = add_market(build_features(C, P, ex, tag))
    return C

OO = prep(sys.argv[2], 'OOT')
IS = prep(sys.argv[1], 'IS') if sys.argv[1] != 'none' else None
if IS is None:
    IS = OO.copy()   # placeholder so sweep() runs; ignore IS columns
KS = (0, 1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32)
de.FILL = 1.0
IS['Z'] = 0.0; OO['Z'] = 0.0
print('\n=== k=0 baselines at model credit (fill 1.0x model) ===')
print('G ticker cell :', sweep(IS, OO, 'Z', 'EV', ks=(0,)).to_string(index=False, float_format=fmt))
print('G shrunk post :', sweep(IS, OO, 'Z', 'EV_post', ks=(0,)).to_string(index=False, float_format=fmt))
for dc in ['D_emp_iv', 'D_emp_iv_S', 'D_cert_iv', 'D_shrink', 'D_est']:
    if dc == 'D_cert_iv':
        for C in (IS, OO):
            from diag import iv_triple
            Q = iv_triple(C, C.IV.values, C.long_IV.fillna(C.IV).values); C['D_cert_iv'] = -np.log(np.clip(Q[:, 0], 1e-6, 1))
    print(f'\n=== {dc} (fill 1.0x model, thr 0.05) ===')
    print('IS :', quintile_table(IS, dc)); print('OOT:', quintile_table(OO, dc))
    print(sweep(IS, OO, dc, ks=KS).to_string(index=False, float_format=fmt))
print('\n=== D_emp_iv fill stress (x model credit) ===')
for f in (1.1, 1.0, 0.9, 0.8):
    de.FILL = f; r = sweep(IS, OO, 'D_emp_iv', ks=(0, 4, 8, 12, 16, 24)); r.insert(0, 'fill', f); print(r.to_string(index=False, float_format=fmt))
de.FILL = 1.0
IS.to_parquet(f'{HERE}/featS_IS.parquet'); OO.to_parquet(f'{HERE}/featS_OOT.parquet')
