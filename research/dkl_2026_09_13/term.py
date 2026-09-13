"""Front expiry vs next expiry: D_term = D(Q_front || Q_back), Q_back = triple at these strikes with the smile level
replaced by the NEXT expiry's ATM vol.  Fires (signed) when the front week is priced richer than the term structure:
event premium the history-based belief cannot see."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, select, kl
from diag import iv_triple
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
C = pd.read_parquet(f'{HERE}/featM4.parquet'); C['entry_date'] = pd.to_datetime(C.entry_date); C['expiry_date'] = pd.to_datetime(C.expiry_date); C['Z'] = 0.0
Tm = pd.read_parquet(f'{HERE}/term_atm.parquet').rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date'})
Tm['entry_date'] = pd.to_datetime(Tm.entry_date); Tm['ExpirationDate'] = pd.to_datetime(Tm.ExpirationDate)
Tm = Tm[Tm.n >= 4]
# front ATM from the same table (consistency) and the NEXT expiry with DTE in (front DTE, front DTE + 14]
fr = Tm.rename(columns={'ExpirationDate': 'expiry_date', 'atm_iv': 'atm_front'})[['ticker', 'entry_date', 'expiry_date', 'atm_front']]
C = C.merge(fr, on=['ticker', 'entry_date', 'expiry_date'], how='left')
bk = Tm.rename(columns={'atm_iv': 'atm_back', 'DTE': 'dte_back'})[['ticker', 'entry_date', 'dte_back', 'atm_back']]
C = C.merge(bk, on=['ticker', 'entry_date'], how='left')
C = C[(C.dte_back > C.DTE) & (C.dte_back <= C.DTE + 14)].sort_values('dte_back').drop_duplicates(['ticker', 'entry_date', 'expiry_date', 'spread_type', 'short_strike'], keep='first')
print(f'candidates with a back expiry: {len(C):,}; med front ATM {C.atm_front.median():.3f} back {C.atm_back.median():.3f}; front/back ratio quantiles', C.eval('atm_front/atm_back').quantile([.1, .5, .9]).round(3).tolist())
ratio = (C.atm_front / C.atm_back).values
Qb = iv_triple(C, C.iv_fit_short.values / ratio, C.iv_fit_long.values / ratio)      # same smile shape, back-month level
Qi = C[['p_iv', 'q_iv', 'ro_iv']].values; ls_b = Qb[:, 1] + 0.5 * Qb[:, 2]
C['term_ratio'] = ratio
C['D_term_u'] = kl(Qi, Qb); C['D_term'] = np.where(C.ls_iv.values > ls_b, C.D_term_u, 0.0)         # front richer than back
C['D_term_r'] = np.where(C.ls_iv.values > ls_b, kl(Qb, Qi), 0.0)
C['D_term_inv'] = np.where(C.ls_iv.values < ls_b, C.D_term_u, 0.0)                                # front CHEAPER than back
print(f'front richer than back on {(C.D_term>0).mean():.1%}; median nonzero D_term {C.D_term[C.D_term>0].median():.4f}')
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
KS = (0, 1, 2, 4, 8, 16, 32, 64)
for score, bel, lab in (('EV', 'pred_loss_share', 'G on P_emp 52:10'), ('EV_r', 'ls_r', 'G on P_real')):
    print(f'\n##### {lab} #####')
    print(' term_ratio (all) IS :', qt(ISc, 'term_ratio', score, bel, False)); print(' term_ratio (all) OOT:', qt(OOc, 'term_ratio', score, bel, False))
    for dc in ('D_term', 'D_term_r', 'D_term_u', 'D_term_inv'):
        print(f'\n=== {lab}, DKL = {dc} ===\n IS : {qt(ISc, dc, score, bel)}\n OOT: {qt(OOc, dc, score, bel)}')
        for fill, thr in ((1.1, 0.01), (1.0, 0.01)):
            print(f' -- fill {fill} thr {thr} --'); print(sw(dc, score, KS, fill, thr).to_string(index=False, float_format=fmt))
C.to_parquet(f'{HERE}/featT.parquet')
