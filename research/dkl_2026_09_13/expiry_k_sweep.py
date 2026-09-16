"""Wider k sweep for canon-252s vs 104-expiry P_real on full-session closes: yield, Sharpe, DD, Calmar, bull_put share."""
import sys, warnings; warnings.filterwarnings('ignore'); sys.path.insert(0, '.'); sys.path.insert(0, 'research/dkl_2026_09_13')
import numpy as np, pandas as pd, report_mid_canon as rmc
from expiry104_checks import load, book
pd.set_option('display.width', 250)
def st(sel, ey):
    if len(sel) < 20: return dict(n=len(sel))
    s = rmc.build_payload(sel, ey, '')['summary']; wag = sel.max_loss_dollar.sum(); pnl = sel.pnl_per_contract.sum()
    return dict(n=len(sel), pnl=round(pnl), yld=round(100 * pnl / wag, 2), sh=s['qty1_sharpe_weekly'], dd=s['qty1_max_dd'],
                calmar=round(s['qty1_cagr'] / abs(s['qty1_max_dd']), 2) if s['qty1_max_dd'] else None, put=round(100 * (sel.spread_type == 'bull_put').mean(), 1))
for win, ey in (('IS', 2025), ('OOT', 2026)):
    F = load(win)
    V = {'canon 252s': (F.p_ex.values, F.q_ex.values, F.ro_ex.values), '52 exp': (F.p_e52.values, F.q_e52.values, F.ro_e52.values), '104 exp': (F.p_e104.values, F.q_e104.values, F.ro_e104.values)}
    for name, (p, q, ro) in V.items():
        rows = []
        for thr in (0.005, 0.01, 0.015):
            for k in (0, 1, 2, 3, 4, 6, 8, 12, 16):
                x = st(book(F, p, q, ro, float(k), thr, 1.08), ey); x.update(thr=thr, k=k); rows.append(x)
        print(f'\n######## {win} · {name} · fill 1.08 · qty1')
        print(pd.DataFrame(rows)[['thr', 'k', 'n', 'pnl', 'yld', 'sh', 'dd', 'calmar', 'put']].to_string(index=False))
