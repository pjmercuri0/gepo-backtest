"""Mirror-signed market-vs-market DKLs: fire when the wing is CHEAP relative to the other market prediction."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, select, kl
from diag import iv_triple
from synth_credit import Y_MAX, IV_LO, IV_HI
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
C = pd.read_parquet(f'{HERE}/featM2.parquet')
T = np.clip(C.DTE.values, 1, None) / 365.0; S = C.entry_price.values
def iv_from(cc0, cc1, cc2, ss, K):
    y = np.clip(np.log(K / S) / np.sqrt(T) / ss, -Y_MAX, Y_MAX); return np.clip(cc0 + cc1 * y + cc2 * y * y, IV_LO, IV_HI)
Qy = iv_triple(C, np.nan_to_num(iv_from(C.y_c0.values, C.y_c1.values, C.y_c2.values, C.y_sig0.values, C.short_strike.values), nan=-1),
                  np.nan_to_num(iv_from(C.y_c0.values, C.y_c1.values, C.y_c2.values, C.y_sig0.values, C.long_strike.values), nan=-1))
Qi = C[['p_iv', 'q_iv', 'ro_iv']].values; Qf = iv_triple(C, C.sig0.values, C.sig0.values)
oky = np.isfinite(Qy).all(1); ls_y = Qy[:, 1] + 0.5 * Qy[:, 2]; ls_f = Qf[:, 1] + 0.5 * Qf[:, 2]
C['D_skew_cheap'] = np.where(C.ls_iv.values < ls_f, kl(Qf, Qi), 0.0)                       # flat vol says riskier than the smile: wing under-priced
C['D_jump_cheap'] = np.where(oky & (C.ls_iv.values < ls_y), kl(np.nan_to_num(Qy, nan=1/3), Qi), 0.0)   # market lowered its risk price since yesterday
C['skew_signed'] = C.ls_iv.values - ls_f; C['jump_signed'] = np.where(oky, C.ls_iv.values - ls_y, np.nan)
C['Z'] = 0.0
print(f'wing cheap vs flat on {(C.D_skew_cheap>0).mean():.1%}; market cheaper than yesterday on {(C.D_jump_cheap>0).mean():.1%} (of {oky.mean():.0%} covered)')
ISc, OOc = C[C.win == 'IS'], C[C.win == 'OOT']
def qt(df, col, score, bel, positive_only=True):
    d = df.dropna(subset=[col, score])
    if positive_only: d = d[d[col] > 0]
    q = pd.qcut(d[col].rank(method='first'), 5, labels=False)
    g = d.groupby(q).agg(loss=('is_loss', 'mean'), real=('loss_share', 'mean'), b=(bel, 'mean'), EV=(score, 'mean'), x=(col, 'mean'))
    return f"n={len(d)} x {g.x.round(3).tolist()} loss {g.loss.round(3).tolist()} realized-minus-belief {(g.real-g.b).round(3).tolist()} EV {g.EV.round(4).tolist()}"
def sw(dc, score, ks, fill, thr):
    de.FILL, de.THR = fill, thr; rows = []
    for k in ks:
        a = book(select(ISc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2025-12-31')); o = book(select(OOc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2026-08-31'))
        rows.append(dict(k=k, is_n=a['n'], is_fin=a['final'], is_sh=a['sharpe'], is_dd=a['dd'], is_loss=a['loss'], oot_n=o['n'], oot_fin=o['final'], oot_sh=o['sharpe'], oot_dd=o['dd'], oot_loss=o['loss']))
    return pd.DataFrame(rows)
for score, bel, lab in (('EV_r', 'ls_r', 'G on P_real'), ('EV', 'pred_loss_share', 'G on P_emp 52:10')):
    print(f'\n##### {lab}: signed quantities, all candidates (negative = cheap wing / market lowered risk) #####')
    print(' skew_signed IS :', qt(ISc, 'skew_signed', score, bel, False)); print(' skew_signed OOT:', qt(OOc, 'skew_signed', score, bel, False))
    print(' jump_signed IS :', qt(ISc, 'jump_signed', score, bel, False)); print(' jump_signed OOT:', qt(OOc, 'jump_signed', score, bel, False))
    for dc in ('D_skew_cheap', 'D_jump_cheap'):
        print(f'\n=== {lab}, DKL = {dc} (median nonzero {C[dc][C[dc]>0].median():.4f}) ===\n IS : {qt(ISc, dc, score, bel)}\n OOT: {qt(OOc, dc, score, bel)}')
        for fill, thr in ((1.1, 0.01), (1.0, 0.01)):
            print(f' -- fill {fill} thr {thr} --'); print(sw(dc, score, (0, 2, 4, 8, 16, 32, 64, 128, 256), fill, thr).to_string(index=False, float_format=fmt))
C.to_parquet(f'{HERE}/featM3.parquet')
