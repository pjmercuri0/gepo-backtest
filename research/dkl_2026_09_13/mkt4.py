"""Two market predictions for the SAME distance: this wing (smile at +y) vs the mirrored wing (smile at -y).
D_mirror_cheap = D(Q_mirror || Q_this), fires when the market prices THIS direction cheaper than the opposite one."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, select, kl
from diag import iv_triple
from synth_credit import Y_MAX, IV_LO, IV_HI
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
C = pd.read_parquet(f'{HERE}/featM3.parquet')
T = np.clip(C.DTE.values, 1, None) / 365.0; S = C.entry_price.values
def iv_mirror(K):
    y = np.clip(np.log(K / S) / np.sqrt(T) / C.sig0.values, -Y_MAX, Y_MAX)
    return np.clip(C.c0.values - C.c1.values * y + C.c2.values * y * y, IV_LO, IV_HI)     # smile evaluated at -y
Qm = iv_triple(C, iv_mirror(C.short_strike.values), iv_mirror(C.long_strike.values)); Qi = C[['p_iv', 'q_iv', 'ro_iv']].values
ls_m = Qm[:, 1] + 0.5 * Qm[:, 2]
C['mirror_signed'] = C.ls_iv.values - ls_m                                # >0: this side priced richer than the opposite side
C['D_mirror_cheap'] = np.where(C.ls_iv.values < ls_m, kl(Qm, Qi), 0.0)     # this side cheaper than opposite: fires
C['D_mirror_rich'] = np.where(C.ls_iv.values >= ls_m, kl(Qi, Qm), 0.0)
C['D_mirror_u'] = kl(Qm, Qi); C['Z'] = 0.0
print(f'this side cheaper than the mirrored side on {(C.D_mirror_cheap>0).mean():.1%}; median nonzero D_mirror_cheap {C.D_mirror_cheap[C.D_mirror_cheap>0].median():.4f}')
print('by direction: cheap share  bull_put', (C[C.spread_type=="bull_put"].D_mirror_cheap>0).mean().round(3), ' bear_call', (C[C.spread_type=="bear_call"].D_mirror_cheap>0).mean().round(3))
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
KS = (0, 1, 2, 4, 8, 16, 32, 64, 128)
for score, bel, lab in (('EV_r', 'ls_r', 'G on P_real'), ('EV', 'pred_loss_share', 'G on P_emp 52:10')):
    print(f'\n##### {lab} #####')
    print(' mirror_signed (all) IS :', qt(ISc, 'mirror_signed', score, bel, False)); print(' mirror_signed (all) OOT:', qt(OOc, 'mirror_signed', score, bel, False))
    for st in ('bull_put', 'bear_call'):
        print(f'   {st} IS :', qt(ISc[ISc.spread_type == st], 'mirror_signed', score, bel, False))
    for dc in ('D_mirror_cheap', 'D_mirror_rich', 'D_mirror_u'):
        print(f'\n=== {lab}, DKL = {dc} ===\n IS : {qt(ISc, dc, score, bel)}\n OOT: {qt(OOc, dc, score, bel)}')
        for fill, thr in ((1.1, 0.01), (1.0, 0.01)):
            print(f' -- fill {fill} thr {thr} --'); print(sw(dc, score, KS, fill, thr).to_string(index=False, float_format=fmt))
C.to_parquet(f'{HERE}/featM4.parquet')
