"""Delta band x k sweep for GROUND = EV * exp(-k * D_ent), D_ent = D(Q_bs || U3) = ln3 - H(Q_bs)  (paper Eq. 19).
Full method: fitted-delta strikes, smile-fit credit x1.08, comm $1.30, P_real belief (W-session window), thr 0.01, top-5/day."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de, band_sweep as bsw
from band_checks import book_comm
from dkl_eval import kelly_ell, outcome_of
from diag import iv_triple
pd.set_option('display.width', 250)
U = np.array([[1/3, 1/3, 1/3]])
def build(lo, hi, tgt, A, W=252):
    b = A[A.dfit_short.between(lo, hi) & (A.dfit_long < A.dfit_short) & (A.model_credit > 0.01)].copy(); b['dist'] = (b.dfit_short - tgt).abs()
    C = b.loc[b.groupby(['ticker', 'entry_date', 'expiry_date', 'spread_type'], sort=False)['dist'].idxmin()].copy()
    C['net_credit'] = C.model_credit; C['max_loss'] = (C.width - C.model_credit).round(4); C = C[C.max_loss > 0]
    P = bsw.p_real(C, W); C = C.reset_index(drop=True); C['p'], C['q'], C['ro'] = P[:, 0], P[:, 1], P[:, 2]
    ell = kelly_ell(C.p.fillna(0).values, C.q.fillna(0).values, C.ro.fillna(0).values, C.net_credit.values / C.max_loss.values); C['EV'] = np.exp(ell) - 1; C.loc[C.p.isna(), 'EV'] = np.nan
    Q = np.clip(iv_triple(C, C.iv_fit_short.values, C.iv_fit_long.values), 1e-4, None); Q /= Q.sum(1, keepdims=True)
    C['D_ent'] = (Q * np.log(Q / U)).sum(1); C['outcome'] = outcome_of(C); C['entry_date'] = pd.to_datetime(C.entry_date)
    return C.dropna(subset=['EV'])
def sel(df, k):
    d = df.copy(); d['GR'] = d['EV'] * np.exp(-k * d['D_ent']); d = d[d.GR >= de.THR]
    return d.sort_values(['entry_date', 'GR'], ascending=[True, False]).groupby('entry_date').head(5)
def matched(ISc, OOc, n_target):
    lo, hi = 0.0, 0.3
    for _ in range(30):
        m_ = (lo + hi) / 2; de.THR = m_; n0 = book_comm(sel(ISc, 0), pd.Timestamp('2025-12-31'), 1.30)['n']
        if n0 > n_target: lo = m_
        else: hi = m_
    de.THR = (lo + hi) / 2; b0 = book_comm(sel(ISc, 0), pd.Timestamp('2025-12-31'), 1.30); o0 = book_comm(sel(OOc, 0), pd.Timestamp('2026-08-31'), 1.30); de.THR = 0.01
    return b0, o0
de.FILL, de.THR = 1.08, 0.01
BANDS = [(0.15, 0.25, 0.20), (0.20, 0.30, 0.25), (0.25, 0.35, 0.30), (0.30, 0.40, 0.35), (0.35, 0.45, 0.40), (0.40, 0.50, 0.45), (0.45, 0.55, 0.50), (0.50, 0.60, 0.55), (0.40, 0.60, 0.50)]
KS = (0, 0.4, 0.8, 1.0, 1.2, 1.6, 2.4)
rows = []; frames = {}
for lo, hi, tgt in BANDS:
    ISc = build(lo, hi, tgt, bsw.IS_ALL); OOc = build(lo, hi, tgt, bsw.OO_ALL); frames[(lo, hi)] = (ISc, OOc)
    for k in KS:
        de.THR = 0.01; a = book_comm(sel(ISc, k), pd.Timestamp('2025-12-31'), 1.30); o = book_comm(sel(OOc, k), pd.Timestamp('2026-08-31'), 1.30)
        r = dict(band=f'{lo:.2f}-{hi:.2f}', k=k, is_n=a['n'], is_fin=round(a['final']), is_sh=round(a['sharpe'], 2), is_dd=round(a['dd'], 1), oot_n=o['n'], oot_fin=round(o['final']), oot_sh=round(o['sharpe'], 2), oot_dd=round(o['dd'], 1), ctrl_is=np.nan, ctrl_oot=np.nan)
        if k in (0.8, 1.2):
            b0, o0 = matched(ISc, OOc, a['n']); r['ctrl_is'] = round(b0['sharpe'], 2); r['ctrl_oot'] = round(o0['sharpe'], 2)
        rows.append(r)
    print(f'band {lo:.2f}-{hi:.2f} done', flush=True)
R = pd.DataFrame(rows); R.to_csv(f'{HERE}/band_ent.csv', index=False)
print('\n===== GROUND = EV * exp(-k * D_ent): delta band x k   (fill 1.08x model, comm $1.30, thr 0.01, top-5/day; ctrl = k=0 at matched trade count) =====')
for k in KS:
    print(f'\n-- k = {k} --'); print(R[R.k == k].drop(columns=['k']).to_string(index=False))
print('\n===== belief window W (sessions) on the two best bands, k = 1.0 =====')
for (lo, hi, tgt) in [(0.50, 0.60, 0.55), (0.40, 0.60, 0.50)]:
    for W in (126, 252, 504):
        ISc = build(lo, hi, tgt, bsw.IS_ALL, W); OOc = build(lo, hi, tgt, bsw.OO_ALL, W); de.THR = 0.01
        a0 = book_comm(sel(ISc, 0), pd.Timestamp('2025-12-31'), 1.30); a = book_comm(sel(ISc, 1.0), pd.Timestamp('2025-12-31'), 1.30)
        o0 = book_comm(sel(OOc, 0), pd.Timestamp('2026-08-31'), 1.30); o = book_comm(sel(OOc, 1.0), pd.Timestamp('2026-08-31'), 1.30)
        print(f'  band {lo:.2f}-{hi:.2f} W={W}: k=0 IS {a0["sharpe"]:5.2f} (n {a0["n"]}, DD {a0["dd"]:.1f}) OOT {o0["sharpe"]:5.2f} | k=1 IS {a["sharpe"]:5.2f} (n {a["n"]}, DD {a["dd"]:.1f}) OOT {o["sharpe"]:5.2f} (DD {o["dd"]:.1f})')
