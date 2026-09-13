"""Three levers on top of the calibrated-market belief, at fillable credit (1.1x model ~ real fills), thr 0.01.
A. SKEW: smile slope c1 (per chain, robust) signed by direction -> D_skew = D(Q_iv(fitted smile) || Q_iv(flat ATM vol)) signed
B. REGIME: market-wide realized vs implied vol -> D_reg = signed KL of 1-week binomial at RV vs at IV (same on all names that day)
C. NAME VRP: does a ticker's realized/market ratio persist? (window-to-window correlation)"""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, select, kl, kelly_ell
from diag import iv_triple
from synth_credit import ncdf
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
C = pd.read_parquet(f'{HERE}/featC.parquet'); C['entry_date'] = pd.to_datetime(C.entry_date)
assert 'c1' in C.columns
T = np.clip(C.DTE.values, 1, None) / 365.0; S = C.entry_price.values; bp = (C.spread_type == 'bull_put').values
# ---- A. skew divergence: smile triple (already p_iv,q_iv,ro_iv) vs FLAT ATM vol triple at same strikes
Qflat = iv_triple(C, C.sig0.values, C.sig0.values)
Qs = C[['p_iv', 'q_iv', 'ro_iv']].values
ls_flat = Qflat[:, 1] + 0.5 * Qflat[:, 2]
C['D_skew_u'] = kl(Qs, Qflat)
C['D_skew'] = np.where(C.ls_iv.values > ls_flat, C.D_skew_u, 0.0)        # smile makes THIS side riskier than flat vol
C['skew_dir'] = np.where(bp, -C.c1, C.c1)                                # >0 = this side's wing is rich
# ---- B. regime: market RV(5d) from the names' own entry prices vs market IV = median fitted ATM vol
px = C.drop_duplicates(['ticker', 'entry_date'])[['ticker', 'entry_date', 'entry_price']].sort_values(['ticker', 'entry_date'])
px['r'] = px.groupby('ticker').entry_price.transform(lambda s: np.log(s).diff())
px['rv5'] = px.groupby('ticker').r.transform(lambda s: s.rolling(5).std() * np.sqrt(252))
mkt = px.groupby('entry_date').rv5.median().rename('mkt_rv').to_frame()
mkt['mkt_iv'] = C.drop_duplicates(['ticker', 'entry_date']).groupby('entry_date').sig0.median()
mkt = mkt.shift(1)   # known at entry: yesterday's close-to-close realized vs yesterday's implied
C = C.merge(mkt, left_on='entry_date', right_index=True, how='left')
h = 5 / 252.0
def bern(sig):  # P(|weekly move| > 1 ATM-sigma-week) under normal with vol sig, as a 2-state law
    z = 1.0 / np.sqrt(h) / np.clip(sig, 0.03, 3) * np.sqrt(h) * np.clip(C.mkt_iv.values, 0.03, 3)   # threshold = 1 sigma at IV
    p = 2 * (1 - ncdf(z)); return np.column_stack([1 - p, p])
Prv, Qiv = bern(C.mkt_rv.values), bern(C.mkt_iv.values)
Prv[:, 1] = np.clip(Prv[:, 1], 1e-6, 1 - 1e-6); Prv[:, 0] = 1 - Prv[:, 1]
d = np.where(Prv[:, 0] > 0, Prv[:, 0] * np.log(Prv[:, 0] / Qiv[:, 0]), 0) + Prv[:, 1] * np.log(Prv[:, 1] / Qiv[:, 1])
C['D_reg_u'] = np.clip(np.nan_to_num(d), 0, None)
C['D_reg'] = np.where(C.mkt_rv > C.mkt_iv, C.D_reg_u, 0.0)               # realized ABOVE implied = VRP has failed recently
C['rv_iv'] = C.mkt_rv / C.mkt_iv
# ---- diagnostics
def qtab(df, col, label, nb=5):
    d = df.dropna(subset=[col, 'EV_cal']); q = pd.qcut(d[col].rank(method='first'), nb, labels=False)
    g = d.groupby(q).agg(n=('is_loss', 'size'), x=(col, 'mean'), loss=('is_loss', 'mean'), ls_real=('loss_share', 'mean'), ls_cal=('ls_cal', 'mean'), EV=('EV_cal', 'mean'))
    g['excess'] = g.ls_real - g.ls_cal
    print(f'--- {label}: by {col} quintile ---'); print(g.round(4).T.to_string())
ISc, OOc = C[C.win == 'IS'], C[C.win == 'OOT']
for col in ('skew_dir', 'D_skew', 'rv_iv', 'D_reg'):
    qtab(ISc, col, 'IS'); qtab(OOc, col, 'OOT')
for lab, sub in (('IS', ISc), ('OOT', OOc)):
    for st in ('bull_put', 'bear_call'):
        d = sub[sub.spread_type == st].dropna(subset=['EV_cal']); q = pd.qcut(d.skew_dir.rank(method='first'), 3, labels=False)
        g = d.groupby(q).agg(n=('is_loss', 'size'), skew=('skew_dir', 'mean'), loss=('is_loss', 'mean'), excess=('loss_share', 'mean'))
        g['excess'] = g.excess - d.groupby(q).ls_cal.mean()
        print(f'{lab} {st} by skew tercile (this side rich):'); print(g.round(4).T.to_string())
# ---- C. name-level VRP persistence
C['ratio_i'] = C.is_loss / C.q_iv.clip(1e-3)
C['half'] = (C.entry_date.dt.year * 2 + (C.entry_date.dt.month > 6)).astype(int)
t = C.groupby(['ticker', 'half']).agg(n=('is_loss', 'size'), loss=('is_loss', 'sum'), q=('q_iv', 'sum')).reset_index()
t = t[t.n >= 40]; t['ratio'] = t.loss / t.q
t['ratio_next'] = t.groupby('ticker').ratio.shift(-1)
tt = t.dropna(); print(f'\nC. name VRP persistence: corr(ratio_h, ratio_h+1) = {np.corrcoef(tt.ratio, tt.ratio_next)[0,1]:.3f}  (n={len(tt)} ticker-halves); ratio sd across tickers {t.ratio.std():.3f}, mean {t.ratio.mean():.3f}')
# ---- k sweeps at fill 1.1, thr 0.01, G on P_cal
de.FILL, de.THR = 1.1, 0.01
def sw(dc, ks):
    rows = []
    for k in ks:
        a = book(select(ISc.dropna(subset=['EV_cal', dc]), 'EV_cal', k, dc), pd.Timestamp('2025-12-31'))
        o = book(select(OOc.dropna(subset=['EV_cal', dc]), 'EV_cal', k, dc), pd.Timestamp('2026-08-31'))
        rows.append(dict(k=k, is_n=a['n'], is_fin=a['final'], is_sh=a['sharpe'], is_dd=a['dd'], is_loss=a['loss'], oot_n=o['n'], oot_fin=o['final'], oot_sh=o['sharpe'], oot_dd=o['dd'], oot_loss=o['loss']))
    return pd.DataFrame(rows)
for dc, ks in (('D_skew', (0, 4, 8, 16, 32, 64, 128)), ('D_skew_u', (0, 4, 8, 16, 32, 64)), ('D_reg', (0, 2, 4, 8, 16, 32, 64)), ('D_reg_u', (0, 2, 4, 8, 16, 32))):
    print(f'\n=== G on P_cal, fill 1.1, thr 0.01, DKL = {dc}  (median nonzero {C[dc][C[dc]>0].median():.4f}) ===')
    print(sw(dc, ks).to_string(index=False, float_format=fmt))
C.to_parquet(f'{HERE}/featL.parquet')
