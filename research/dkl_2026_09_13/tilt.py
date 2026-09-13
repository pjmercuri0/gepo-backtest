"""Belief tilted toward the market along the geometric (exponential-family) path:
   P_k(i) ∝ P_real(i)^(1/(1+k)) * Q(i)^(k/(1+k))       (log P_k = log P - w*log(P/Q) - log Z, w = k/(1+k))
G on P_k.  k=0 -> realized belief; k->inf -> market (EV ~ 0).  Control: arithmetic mixture (P + kQ)/(1+k).
Ceiling: realized loss share regressed on (belief, market) in IS, applied to OOT."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, select, kelly_ell, pnl_of
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
C = pd.read_parquet(f'{HERE}/featRM.parquet'); C['entry_date'] = pd.to_datetime(C.entry_date); C['Z'] = 0.0
Pr = C[['p_r', 'q_r', 'ro_r']].values; Qi = C[['p_iv', 'q_iv', 'ro_iv']].values
cw = np.clip(C.net_credit.values / C.width.values, 1e-3, 0.98)
sh = np.where(C.ls_r.values > 0, C.ro_r.values * 0.5 / np.clip(C.ls_r.values, 1e-6, None), 0.5)
Qc = np.column_stack([1 - cw, cw * (1 - sh), 2 * cw * sh]); Qc = np.clip(Qc, 1e-6, None); Qc /= Qc.sum(1, keepdims=True)
b = C.net_credit.values / C.max_loss.values; okr = (C.n_yr >= 120).values
def tilt(P, Q, k, geometric=True):
    w = k / (1 + k)
    M = np.exp((1 - w) * np.log(np.clip(P, 1e-9, None)) + w * np.log(np.clip(Q, 1e-9, None))) if geometric else (1 - w) * P + w * Q
    return M / M.sum(1, keepdims=True)
KS = (0, 0.25, 0.5, 1, 2, 4, 8)
ISm, OOm = (C.win == 'IS').values, (C.win == 'OOT').values
def run(Q, qname, geometric):
    for fill, thr in ((1.1, 0.01), (1.1, 0.02), (1.0, 0.01)):
        de.FILL, de.THR = fill, thr; rows = []
        for k in KS:
            M = tilt(Pr, Q, k, geometric)
            ell = np.where(okr, kelly_ell(M[:, 0], M[:, 1], M[:, 2], b), np.nan); C['EVk'] = np.exp(ell) - 1; C['lsk'] = M[:, 1] + 0.5 * M[:, 2]
            a = book(select(C[ISm].dropna(subset=['EVk']), 'EVk', 0, 'Z'), pd.Timestamp('2025-12-31')); o = book(select(C[OOm].dropna(subset=['EVk']), 'EVk', 0, 'Z'), pd.Timestamp('2026-08-31'))
            sa = select(C[ISm].dropna(subset=['EVk']), 'EVk', 0, 'Z'); so = select(C[OOm].dropna(subset=['EVk']), 'EVk', 0, 'Z')
            rows.append(dict(k=k, is_n=a['n'], is_fin=a['final'], is_sh=a['sharpe'], is_dd=a['dd'], is_loss=a['loss'], is_cal=float(sa.loss_share.mean() - sa.lsk.mean()) if len(sa) else np.nan,
                             oot_n=o['n'], oot_fin=o['final'], oot_sh=o['sharpe'], oot_dd=o['dd'], oot_loss=o['loss'], oot_cal=float(so.loss_share.mean() - so.lsk.mean()) if len(so) else np.nan))
        print(f'\n=== {"geometric" if geometric else "arithmetic"} tilt toward {qname}, fill {fill}, thr {thr}   (is_cal/oot_cal = realized minus belief loss share on the selected book) ===')
        print(pd.DataFrame(rows).to_string(index=False, float_format=fmt))
for Q, qn in ((Qi, 'Q_iv (fitted surface)'), (Qc, 'Q_cred (credit-implied)')):
    run(Q, qn, True)
run(Qi, 'Q_iv', False)
# ---- ceiling: linear blend fitted in IS
d = C[ISm & okr]; X = np.column_stack([np.ones(len(d)), d.ls_r, d.ls_iv]); y = d.loss_share.values
w = np.linalg.lstsq(X, y, rcond=None)[0]; print(f'\nIS fit: realized loss share = {w[0]:.3f} + {w[1]:.3f}*belief + {w[2]:.3f}*market')
C['ls_fit'] = np.clip(w[0] + w[1] * C.ls_r + w[2] * C.ls_iv, 1e-3, 0.95)
ro_f = np.clip(C.ro_r.values / np.clip(C.ls_r.values, 1e-6, None) * C.ls_fit.values * 0.5 * 2, 0, None); ro_f = np.minimum(ro_f, 2 * C.ls_fit.values)
q_f = C.ls_fit.values - 0.5 * ro_f; p_f = 1 - q_f - ro_f
ell = np.where(okr & (q_f > 0) & (p_f > 0), kelly_ell(p_f, q_f, ro_f, b), np.nan); C['EVf'] = np.exp(ell) - 1
for fill, thr in ((1.1, 0.01), (1.1, 0.02), (1.0, 0.01)):
    de.FILL, de.THR = fill, thr
    a = book(select(C[ISm].dropna(subset=['EVf']), 'EVf', 0, 'Z'), pd.Timestamp('2025-12-31')); o = book(select(C[OOm].dropna(subset=['EVf']), 'EVf', 0, 'Z'), pd.Timestamp('2026-08-31'))
    print(f'CEILING (fitted blend, IS weights) fill {fill} thr {thr}: IS n={a["n"]} ${a["final"]:,.0f} Sh {a["sharpe"]:.2f} DD {a["dd"]:.1f}% | OOT n={o["n"]} ${o["final"]:,.0f} Sh {o["sharpe"]:.2f} DD {o["dd"]:.1f}%')
