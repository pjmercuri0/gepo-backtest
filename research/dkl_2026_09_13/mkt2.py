"""DKL between TWO MARKET predictions of the same spread.  G on history (P_real, and P_emp for comparison).
 D_jump : today's smile vs YESTERDAY's smile (same expiry) evaluated at today's strikes -> the market just repriced this name
 D_skew : smile vs flat ATM vol at these strikes -> skew premium on this side
Signed versions fire when the second prediction says this spread is riskier."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, select, kl
from diag import iv_triple
from synth_credit import Y_MAX, IV_LO, IV_HI
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
C = pd.read_parquet(f'{HERE}/featRM.parquet'); C['entry_date'] = pd.to_datetime(C.entry_date); C['expiry_date'] = pd.to_datetime(C.expiry_date); C['Z'] = 0.0
# yesterday's fit for the same (ticker, expiry): previous entry date's chain fit
F = pd.concat([pd.read_parquet(f'{HERE}/is_synth.parquet'), pd.read_parquet(f'{HERE}/oot_synth.parquet')])[['ticker', 'entry_date', 'expiry_date', 'c0', 'c1', 'c2', 'sig0', 'entry_price']].drop_duplicates(['ticker', 'entry_date', 'expiry_date'])
F['entry_date'] = pd.to_datetime(F.entry_date); F['expiry_date'] = pd.to_datetime(F.expiry_date)
F = F.sort_values(['ticker', 'expiry_date', 'entry_date'])
for c in ('c0', 'c1', 'c2', 'sig0', 'entry_price', 'entry_date'):
    F[f'y_{c}'] = F.groupby(['ticker', 'expiry_date'])[c].shift(1)
F = F[(F.entry_date - F.y_entry_date).dt.days <= 4]
C = C.merge(F[['ticker', 'entry_date', 'expiry_date', 'y_c0', 'y_c1', 'y_c2', 'y_sig0', 'y_entry_price']], on=['ticker', 'entry_date', 'expiry_date'], how='left')
T = np.clip(C.DTE.values, 1, None) / 365.0; S = C.entry_price.values
def iv_from(cc0, cc1, cc2, ss, K):
    y = np.clip(np.log(K / S) / np.sqrt(T) / ss, -Y_MAX, Y_MAX); return np.clip(cc0 + cc1 * y + cc2 * y * y, IV_LO, IV_HI)
ivy_s = iv_from(C.y_c0.values, C.y_c1.values, C.y_c2.values, C.y_sig0.values, C.short_strike.values)
ivy_l = iv_from(C.y_c0.values, C.y_c1.values, C.y_c2.values, C.y_sig0.values, C.long_strike.values)
Qy = iv_triple(C, np.nan_to_num(ivy_s, nan=-1), np.nan_to_num(ivy_l, nan=-1))
Qi = C[['p_iv', 'q_iv', 'ro_iv']].values
Qf = iv_triple(C, C.sig0.values, C.sig0.values)
oky = np.isfinite(Qy).all(1)
ls_y = Qy[:, 1] + 0.5 * Qy[:, 2]; ls_f = Qf[:, 1] + 0.5 * Qf[:, 2]
C['D_jump_u'] = np.where(oky, kl(Qi, np.nan_to_num(Qy, nan=1/3)), 0.0); C['D_jump'] = np.where(oky & (C.ls_iv.values > ls_y), C.D_jump_u, 0.0)
C['D_jumpr_u'] = np.where(oky, kl(np.nan_to_num(Qy, nan=1/3), Qi), 0.0); C['D_jumpr'] = np.where(oky & (C.ls_iv.values > ls_y), C.D_jumpr_u, 0.0)
C['D_skew_u'] = kl(Qi, Qf); C['D_skew'] = np.where(C.ls_iv.values > ls_f, C.D_skew_u, 0.0)
C['iv_chg'] = np.where(oky, C.ls_iv.values - ls_y, np.nan)
print(f'yesterday-fit coverage {oky.mean():.1%}; market says riskier than yesterday on {(C.D_jump>0).mean():.1%}; skew makes this side riskier on {(C.D_skew>0).mean():.1%}')
ISc, OOc = C[C.win == 'IS'], C[C.win == 'OOT']
def qt(df, col, score, bel):
    d = df.dropna(subset=[col, score]); nz = d[col] > 0
    if nz.mean() < 0.9: d = d[nz]
    q = pd.qcut(d[col].rank(method='first'), 5, labels=False)
    g = d.groupby(q).agg(loss=('is_loss', 'mean'), real=('loss_share', 'mean'), b=(bel, 'mean'), EV=(score, 'mean'))
    return f"n={len(d)} loss {g.loss.round(3).tolist()} realized-minus-belief {(g.real-g.b).round(3).tolist()} EV {g.EV.round(4).tolist()}"
def sw(dc, score, ks, fill, thr):
    de.FILL, de.THR = fill, thr; rows = []
    for k in ks:
        a = book(select(ISc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2025-12-31')); o = book(select(OOc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2026-08-31'))
        rows.append(dict(k=k, is_n=a['n'], is_fin=a['final'], is_sh=a['sharpe'], is_dd=a['dd'], is_loss=a['loss'], oot_n=o['n'], oot_fin=o['final'], oot_sh=o['sharpe'], oot_dd=o['dd'], oot_loss=o['loss']))
    return pd.DataFrame(rows)
KS = (0, 2, 4, 8, 16, 32, 64, 128)
for score, bel, lab in (('EV_r', 'ls_r', 'G on P_real'), ('EV', 'pred_loss_share', 'G on P_emp 52:10')):
    for dc in ('D_jump', 'D_jump_u', 'D_jumpr', 'D_skew', 'D_skew_u'):
        print(f'\n=== {lab}, DKL = {dc} (median nonzero {C[dc][C[dc]>0].median():.4f}) ===\n IS : {qt(ISc, dc, score, bel)}\n OOT: {qt(OOc, dc, score, bel)}')
        print(sw(dc, score, KS, 1.1, 0.01).to_string(index=False, float_format=fmt))
C.to_parquet(f'{HERE}/featM2.parquet')
