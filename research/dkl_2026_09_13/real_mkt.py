"""G on P_real (realized moves vs today's strikes).  DKL = D(P_real || Q_market).
Q_iv   = fitted-surface N(d2) triple for these strikes (the market's price of risk, validated vs fills).
Q_cred = model-free: implied by the fill credit c on width w: q = c/w (2-state fair value), split by P_real's ro share."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, select, kl
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
C = pd.read_parquet(f'{HERE}/featR2.parquet'); C['entry_date'] = pd.to_datetime(C.entry_date)
Pr = C[['p_r', 'q_r', 'ro_r']].values; Qi = C[['p_iv', 'q_iv', 'ro_iv']].values
cw = np.clip(C.net_credit.values / C.width.values, 1e-3, 0.98)                   # credit-implied loss share
sh = np.where(C.ls_r.values > 0, C.ro_r.values * 0.5 / np.clip(C.ls_r.values, 1e-6, None), 0.5)  # partial share of loss-share, from P_real
q_c = cw * (1 - sh); ro_c = 2 * cw * sh; Qc = np.column_stack([1 - q_c - ro_c, q_c, ro_c]); Qc = np.clip(Qc, 1e-6, None); Qc /= Qc.sum(1, keepdims=True)
C['ls_cred'] = cw
C['D_r_iv'] = kl(Pr, Qi); C['D_iv_r'] = kl(Qi, Pr)
C['D_r_iv_S'] = np.where(C.ls_iv > C.ls_r, C.D_r_iv, 0.0)          # market prices MORE risk than realized history
C['D_r_iv_H'] = np.where(C.ls_iv <= C.ls_r, C.D_r_iv, 0.0)         # realized history riskier than market prices
C['D_r_cred'] = kl(Pr, Qc); C['D_cred_r'] = kl(Qc, Pr)
C['D_r_cred_S'] = np.where(C.ls_cred > C.ls_r, C.D_r_cred, 0.0)
C['D_cert_iv'] = -np.log(np.clip(C.p_iv, 1e-6, 1)); C['Z'] = 0.0
ok = C.EV_r.notna()
print(f'valid P_real G: {ok.mean():.1%}; market prices more risk than realized history on {(C.ls_iv > C.ls_r).mean():.1%}; med D_r_iv {C.D_r_iv.median():.4f}')
ISc, OOc = C[C.win == 'IS'], C[C.win == 'OOT']
def qt(df, col):
    d = df.dropna(subset=[col, 'EV_r']); nz = d[col] > 0
    if nz.mean() < 0.9: d = d[nz]
    q = pd.qcut(d[col].rank(method='first'), 5, labels=False)
    g = d.groupby(q).agg(loss=('is_loss', 'mean'), real=('loss_share', 'mean'), bel=('ls_r', 'mean'), EV=('EV_r', 'mean'))
    return f"loss {g.loss.round(3).tolist()}  realized-minus-belief {(g.real-g.bel).round(3).tolist()}  EV {g.EV.round(4).tolist()}"
def sw(dc, ks, fill, thr, score='EV_r'):
    de.FILL, de.THR = fill, thr; rows = []
    for k in ks:
        a = book(select(ISc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2025-12-31')); o = book(select(OOc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2026-08-31'))
        rows.append(dict(k=k, is_n=a['n'], is_fin=a['final'], is_sh=a['sharpe'], is_dd=a['dd'], is_loss=a['loss'], oot_n=o['n'], oot_fin=o['final'], oot_sh=o['sharpe'], oot_dd=o['dd'], oot_loss=o['loss']))
    return pd.DataFrame(rows)
KS = (0, 1, 2, 4, 6, 8, 12, 16, 24, 32)
for dc in ('D_r_iv', 'D_r_iv_S', 'D_r_iv_H', 'D_iv_r', 'D_r_cred', 'D_r_cred_S', 'D_cred_r', 'D_cert_iv'):
    print(f'\n=== G on P_real, DKL = {dc} ===\n IS : {qt(ISc, dc)}\n OOT: {qt(OOc, dc)}')
    for fill, thr in ((1.1, 0.02), (1.1, 0.01), (1.0, 0.02)):
        print(f' -- fill {fill} thr {thr} --'); print(sw(dc, KS, fill, thr).to_string(index=False, float_format=fmt))
C.to_parquet(f'{HERE}/featRM.parquet')
