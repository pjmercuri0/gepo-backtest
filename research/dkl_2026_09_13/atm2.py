import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import kl
from diag import iv_triple
from band_checks import book_comm
from synth_credit import ncdf
pd.set_option('display.width', 250)
C = pd.read_parquet(f'{HERE}/featATM.parquet'); C['entry_date'] = pd.to_datetime(C.entry_date); C['expiry_date'] = pd.to_datetime(C.expiry_date); C['Z'] = 0.0
T = np.clip(C.DTE.values, 1, None) / 365.0; S = C.entry_price.values
Qi = iv_triple(C, C.iv_fit_short.values, C.iv_fit_long.values); ls_i = Qi[:, 1] + 0.5 * Qi[:, 2]
# ---- (a) term coverage diagnosis
Tm = pd.read_parquet(f'{HERE}/term_atm.parquet').rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date'}); Tm['entry_date'] = pd.to_datetime(Tm.entry_date); Tm['ExpirationDate'] = pd.to_datetime(Tm.ExpirationDate)
print(f'term table: {len(Tm):,} chains, n>=4 on {(Tm.n>=4).mean():.1%}, DTE dist {Tm.DTE.describe()[["min","25%","50%","75%","max"]].round(0).to_dict()}')
key = ['ticker', 'entry_date']
bk = Tm[Tm.n >= 3].rename(columns={'atm_iv': 'atm_back', 'DTE': 'dte_back'})[key + ['dte_back', 'atm_back']]
B = C[key + ['DTE', 'expiry_date', 'spread_type', 'short_strike']].merge(bk, on=key, how='left')
B = B[(B.dte_back > B.DTE) & (B.dte_back <= B.DTE + 21)].sort_values('dte_back').drop_duplicates(['ticker', 'entry_date', 'expiry_date', 'spread_type', 'short_strike'])
C = C.drop(columns=[c for c in ('atm_back', 'atm_front') if c in C.columns]).merge(B[['ticker', 'entry_date', 'expiry_date', 'spread_type', 'short_strike', 'atm_back', 'dte_back']], on=['ticker', 'entry_date', 'expiry_date', 'spread_type', 'short_strike'], how='left')
okb = C.atm_back.notna().values
print(f'back-expiry coverage with n>=3 and <=21 days: {okb.mean():.1%}; median back DTE {C.dte_back.median()}; front/back ratio quantiles {(C.sig0/C.atm_back).quantile([.1,.5,.9]).round(3).tolist()}')
ratio = (C.sig0 / C.atm_back).values
Qb = iv_triple(C, np.nan_to_num(C.iv_fit_short.values / ratio, nan=-1), np.nan_to_num(C.iv_fit_long.values / ratio, nan=-1)); ls_b = Qb[:, 1] + 0.5 * Qb[:, 2]
C['D_term'] = np.where(okb, kl(Qi, np.nan_to_num(Qb, nan=1/3)), 0.0)
C['D_term_rich'] = np.where(okb & (ls_i > ls_b), C.D_term, 0.0); C['D_term_cheap'] = np.where(okb & (ls_i < ls_b), C.D_term, 0.0)
C['term_ratio'] = np.where(okb, ratio, np.nan)
# ---- (b) name-level variance premium: realized exceedance of 1 implied sigma over the matching horizon, trailing 252d
import band_sweep as bsw
PX = bsw.PX; out = np.full(len(C), np.nan); C = C.reset_index(drop=True)
for tk, g in C.groupby('ticker'):
    s = PX.get(tk)
    if s is None or len(s) < 60: continue
    dates = s.index.values; cl = s.values; pos = np.clip(np.searchsorted(dates, g.entry_date.values), 0, len(cl) - 1)
    for d in (1, 2, 3, 4):
        m = (g.DTE.clip(1, 4).astype(int) == d).values
        if not m.any(): continue
        R = np.abs(np.log(cl[d:] / cl[:-d])); p0 = pos[m]; sub = g[m]
        thr = sub.sig0.values * np.sqrt(d / 365.0)
        idx = (p0 - d - 1)[:, None] - np.arange(252)[None, :]; ok = idx >= 0
        r = np.where(ok, R[np.clip(idx, 0, len(R) - 1)], np.nan)
        n = ok.sum(1); ex = ((r > thr[:, None]) & ok).sum(1)
        val = (ex + 0.5) / (n + 1.0); val[n < 120] = np.nan; out[sub.index.values] = val
C['p_exceed'] = out; okv = np.isfinite(out)
p_iv = 2 * (1 - ncdf(1.0))
Pr2 = np.column_stack([1 - C.p_exceed.fillna(p_iv), C.p_exceed.fillna(p_iv)]); Qi2 = np.array([[1 - p_iv, p_iv]])
C['D_vrp'] = np.where(okv, (Pr2 * np.log(Pr2 / Qi2)).sum(1), 0.0)
C['D_vrp_pos'] = np.where(okv & (C.p_exceed < p_iv), C.D_vrp, 0.0)      # realized calmer than implied: premium present
C['D_vrp_neg'] = np.where(okv & (C.p_exceed > p_iv), C.D_vrp, 0.0)      # realized wilder than implied
print(f'VRP coverage {okv.mean():.1%}; median realized exceedance {np.nanmedian(out):.3f} vs implied {p_iv:.3f}; premium present on {(C.D_vrp_pos>0).mean():.1%}')
# ---- (c) today's implied law vs the name's TYPICAL law (trailing-252d median ATM IV of the name)
F = pd.concat([pd.read_parquet(f'{HERE}/is_synth.parquet'), pd.read_parquet(f'{HERE}/oot_synth.parquet')])[['ticker', 'entry_date', 'sig0']].drop_duplicates(['ticker', 'entry_date'])
F['entry_date'] = pd.to_datetime(F.entry_date); F = F.sort_values(['ticker', 'entry_date'])
F['sig_typ'] = F.groupby('ticker').sig0.transform(lambda s: s.shift(1).rolling(252, min_periods=60).median())
C = C.merge(F[['ticker', 'entry_date', 'sig_typ']], on=['ticker', 'entry_date'], how='left'); okt = C.sig_typ.notna().values
rt = (C.sig0 / C.sig_typ).values
Qt = iv_triple(C, np.nan_to_num(C.iv_fit_short.values / rt, nan=-1), np.nan_to_num(C.iv_fit_long.values / rt, nan=-1)); ls_t = Qt[:, 1] + 0.5 * Qt[:, 2]
C['D_ivr'] = np.where(okt, kl(Qi, np.nan_to_num(Qt, nan=1/3)), 0.0)
C['D_ivr_hi'] = np.where(okt & (ls_i > ls_t), C.D_ivr, 0.0); C['D_ivr_lo'] = np.where(okt & (ls_i < ls_t), C.D_ivr, 0.0)
print(f'IV-rank coverage {okt.mean():.1%}; IV above typical on {(C.D_ivr_hi>0).mean():.1%}')
ISc, OOc = C[C.win == 'IS'].dropna(subset=['EV']).copy(), C[C.win == 'OOT'].dropna(subset=['EV']).copy()
for c in (ISc, OOc): c['is_loss'] = (c.outcome == 'LOSS').astype(float)
de.FILL, de.THR = 1.08, 0.01
def sel(df, k, dc):
    d = df.copy(); d['GR'] = d['EV'] * np.exp(k * d[dc]); d = d[d.GR >= de.THR]
    return d.sort_values(['entry_date', 'GR'], ascending=[True, False]).groupby('entry_date').head(5)
def qt(df, col, pos=True):
    d = df[df[col] > 0] if pos and (df[col] > 0).mean() < 0.9 else df
    if len(d) < 200: return 'n<200'
    q = pd.qcut(d[col].rank(method='first'), 5, labels=False); g = d.groupby(q).agg(loss=('is_loss', 'mean'), EV=('EV', 'mean'), x=(col, 'mean'))
    return f"n={len(d)} x {g.x.round(4).tolist()} loss {g.loss.round(3).tolist()} EV {g.EV.round(4).tolist()}"
for raw in ('term_ratio', 'p_exceed'):
    print(f'\n{raw} (all): IS {qt(ISc.dropna(subset=[raw]), raw, False)}\n{" "*len(raw)}        OOT {qt(OOc.dropna(subset=[raw]), raw, False)}')
for dc in ('D_term', 'D_term_rich', 'D_term_cheap', 'D_vrp', 'D_vrp_pos', 'D_vrp_neg', 'D_ivr', 'D_ivr_hi', 'D_ivr_lo'):
    print(f'\n=== 50-60 delta, DKL = {dc} (median nonzero {C[dc][C[dc]>0].median():.4f})  (k<0 discount, k>0 reward) ===\n IS : {qt(ISc, dc)}\n OOT: {qt(OOc, dc)}')
    rows = []
    for k in (-64, -16, -8, -4, 0, 4, 8, 16, 64):
        a = book_comm(sel(ISc, k, dc), pd.Timestamp('2025-12-31'), 1.30); o = book_comm(sel(OOc, k, dc), pd.Timestamp('2026-08-31'), 1.30)
        rows.append(dict(k=k, is_n=a['n'], is_fin=round(a['final']), is_sh=round(a['sharpe'], 2), is_dd=round(a['dd'], 1), oot_n=o['n'], oot_fin=round(o['final']), oot_sh=round(o['sharpe'], 2), oot_dd=round(o['dd'], 1)))
    print(pd.DataFrame(rows).to_string(index=False))
C.to_parquet(f'{HERE}/featATM2.parquet')
