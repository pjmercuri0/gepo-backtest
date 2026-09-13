"""Belief = CALIBRATED MARKET.  Q_iv (fitted-surface N(d2) triple) is scaled by realized/market
ratios learned causally in the market's own risk buckets (fitted short delta x DTE x IV-rank tercile,
trailing 52 expiries).  G on P_cal at fillable (model) credit.  DKL candidates measured against it."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import kelly_ell, kl, book, select, quintile_table, THR
from diag import iv_triple
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
NW, MIN_N = 52, 30
IS = pd.read_parquet(f'{HERE}/featS_IS.parquet'); OO = pd.read_parquet(f'{HERE}/featS_OOT.parquet')
IS['win'] = 'IS'; OO['win'] = 'OOT'
C = pd.concat([IS, OO], ignore_index=True)
C['entry_date'] = pd.to_datetime(C.entry_date); C['expiry_date'] = pd.to_datetime(C.expiry_date)
Q = iv_triple(C, C.IV.values, C.long_IV.fillna(C.IV).values)
C['p_iv'], C['q_iv'], C['ro_iv'] = Q[:, 0], Q[:, 1], Q[:, 2]
C = C.dropna(subset=['p_iv']).copy()
C['is_partial'] = (C.outcome == 'PARTIAL').astype(float)
C['dbin'] = pd.cut(C.d_sh, [0.0999, 0.15, 0.20, 0.25, 0.3001], labels=False)
ivr = C.iv_rank.fillna(C.iv_rank.median()); ivr = ivr / 100.0 if ivr.max() > 1.5 else ivr
C['ivb'] = pd.cut(ivr, [-0.01, 0.33, 0.67, 1.01], labels=False)
C['dte_i'] = C.DTE.clip(1, 4).astype(int)
ex = np.sort(C.expiry_date.unique()); C['eidx'] = np.searchsorted(ex, C.expiry_date.values)
nE = np.searchsorted(ex, C.entry_date.values, side='left'); lo = np.clip(nE - NW, 0, None)

def cum_ratio(keys):
    """per key: cumulative sums over expiry index of [loss, q_iv, partial, ro_iv, n]"""
    if keys:
        kf = C[keys].drop_duplicates().reset_index(drop=True); kf['kid'] = np.arange(len(kf))
        kid = C[keys].merge(kf, on=keys, how='left').kid.values; nk = len(kf)
    else:
        kid = np.zeros(len(C), dtype=int); nk = 1
    A = np.zeros((nk, len(ex) + 1, 5))
    for j, col in enumerate(['is_loss', 'q_iv', 'is_partial', 'ro_iv']):
        np.add.at(A[:, :, j], (kid, C.eidx.values + 1), C[col].values)
    np.add.at(A[:, :, 4], (kid, C.eidx.values + 1), 1.0)
    A = np.cumsum(A, axis=1)
    return A[kid, nE] - A[kid, lo]

full = cum_ratio(['dbin', 'dte_i', 'ivb']); mid = cum_ratio(['dbin', 'dte_i']); glob = cum_ratio([])
S = np.where((full[:, 4] >= MIN_N)[:, None], full, np.where((mid[:, 4] >= MIN_N)[:, None], mid, glob))
with np.errstate(invalid='ignore', divide='ignore'):
    r_loss = S[:, 0] / S[:, 1]; r_par = S[:, 2] / S[:, 3]
ok = S[:, 4] >= MIN_N
C['r_loss'] = np.where(ok, r_loss, np.nan); C['r_par'] = np.where(ok, r_par, np.nan)
C['q_cal'] = np.clip(C.q_iv * C.r_loss, 1e-4, 0.98); C['ro_cal'] = np.clip(C.ro_iv * C.r_par, 1e-4, 0.98)
C['p_cal'] = np.clip(1 - C.q_cal - C.ro_cal, 1e-4, None)
s = C.p_cal + C.q_cal + C.ro_cal; C['p_cal'] /= s; C['q_cal'] /= s; C['ro_cal'] /= s
b = C.net_credit.values / C.max_loss.values
C['ell_cal'] = kelly_ell(C.p_cal.values, C.q_cal.values, C.ro_cal.values, b); C['EV_cal'] = np.exp(C.ell_cal) - 1
C['ell_mkt'] = kelly_ell(C.p_iv.values, C.q_iv.values, C.ro_iv.values, b); C['EV_mkt'] = np.exp(C.ell_mkt) - 1
Pc = C[['p_cal', 'q_cal', 'ro_cal']].values; Qi = C[['p_iv', 'q_iv', 'ro_iv']].values; Pe = C[['p', 'q', 'ro']].values
C['ls_cal'] = C.q_cal + 0.5 * C.ro_cal
C['D_cal_iv'] = kl(Pc, Qi); C['D_iv_cal'] = kl(Qi, Pc)
C['D_cal_iv_S'] = np.where(C.ls_cal > C.ls_iv, C.D_cal_iv, 0.0)        # calibrated belief RISKIER than market
C['D_emp_cal'] = np.where(np.isfinite(Pe).all(1), kl(np.nan_to_num(Pe, nan=1/3), Pc), 0.0)   # history vs calibrated market
C['D_cert_iv'] = -np.log(np.clip(C.p_iv, 1e-6, 1))
C['D_cal_emp_S'] = np.where(C.ls_cal > C.pred_loss_share.fillna(0), C.D_emp_cal, 0.0)
C['Z'] = 0.0
v = C[C.EV_cal.notna()]
print(f'rows {len(C):,}; calibration ratio loss med {C.r_loss.median():.3f} partial {C.r_par.median():.3f}; valid G(cal) {C.EV_cal.notna().mean():.1%}')
print(f'mean loss share: realized {C.loss_share.mean():.3f}  market {C.ls_iv.mean():.3f}  calibrated {C.ls_cal.mean():.3f}  P_emp {C.pred_loss_share.mean():.3f}')
print(f'corr(EV_cal, EV_emp) {np.corrcoef(v.EV_cal, v.EV.fillna(0))[0,1]:.3f}; EV_cal quintile realized pnl at 1.0x:')
v2 = v.copy(); v2['pnl'] = de.pnl_of(v2, v2.net_credit.values, v2.max_loss.values)
print(v2.groupby(pd.qcut(v2.EV_cal.rank(method='first'), 5, labels=False)).agg(n=('pnl','size'), EV_cal=('EV_cal','mean'), pnl=('pnl','mean'), loss=('is_loss','mean'), ls_cal=('ls_cal','mean'), ls_real=('loss_share','mean')).round(3).T.to_string())
ISc, OOc = C[C.win == 'IS'].copy(), C[C.win == 'OOT'].copy()
def sw(dc, score, ks, fill):
    de.FILL = fill
    rows = []
    for k in ks:
        a = book(select(ISc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2025-12-31'))
        o = book(select(OOc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2026-08-31'))
        rows.append(dict(k=k, is_n=a['n'], is_fin=a['final'], is_sh=a['sharpe'], is_dd=a['dd'], is_loss=a['loss'], oot_n=o['n'], oot_fin=o['final'], oot_sh=o['sharpe'], oot_dd=o['dd'], oot_loss=o['loss']))
    de.FILL = 1.0
    return pd.DataFrame(rows)
KS = (0, 1, 2, 4, 8, 12, 16, 24)
for fill in (1.0, 1.1):
    print(f'\n################ fill = {fill:.1f} x model credit ################')
    print('k=0 G on P_emp (52:10 belief):'); print(sw('Z', 'EV', (0,), fill).to_string(index=False, float_format=fmt))
    print('k=0 G on P_cal (calibrated market):'); print(sw('Z', 'EV_cal', (0,), fill).to_string(index=False, float_format=fmt))
    print('k=0 G on raw market Q_iv:'); print(sw('Z', 'EV_mkt', (0,), fill).to_string(index=False, float_format=fmt))
    for dc in ['D_cal_iv', 'D_cal_iv_S', 'D_iv_cal', 'D_emp_cal', 'D_cal_emp_S', 'D_cert_iv']:
        print(f'\n=== G on P_cal, DKL = {dc} ===')
        if fill == 1.0:
            print('IS :', quintile_table(ISc.assign(EV=ISc.EV_cal, q=ISc.q_cal, pred_loss_share=ISc.ls_cal), dc))
            print('OOT:', quintile_table(OOc.assign(EV=OOc.EV_cal, q=OOc.q_cal, pred_loss_share=OOc.ls_cal), dc))
        print(sw(dc, 'EV_cal', KS, fill).to_string(index=False, float_format=fmt))
C.to_parquet(f'{HERE}/featC.parquet')
