"""DKL at the money (50-60 delta), fill 1.08x model, commission $1.30, thr 0.01, G on P_real. Both signs of k."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de, band_sweep as bsw
from band_checks import build, book_comm
from dkl_eval import kl
from diag import iv_triple
from synth_credit import Y_MAX, IV_LO, IV_HI
pd.set_option('display.width', 250); fmt = lambda x: f'{x:.2f}'
LO, HI, TGT = 0.50, 0.60, 0.55
frames = {}
for lab, A in (('IS', bsw.IS_ALL), ('OOT', bsw.OO_ALL)):
    C = build(LO, HI, TGT, A); C['win'] = lab; frames[lab] = C
C = pd.concat(frames.values(), ignore_index=True); C['entry_date'] = pd.to_datetime(C.entry_date); C['expiry_date'] = pd.to_datetime(C.expiry_date)
# market triples
Qi = iv_triple(C, C.iv_fit_short.values, C.iv_fit_long.values); Pr = C[['p', 'q', 'ro']].values
C['ls_iv'] = Qi[:, 1] + 0.5 * Qi[:, 2]; C['ls_r'] = C.q + 0.5 * C.ro
C['D_r_iv'] = kl(Pr, Qi); C['D_iv_r'] = kl(Qi, Pr)
C['D_r_iv_S'] = np.where(C.ls_iv > C.ls_r, C.D_r_iv, 0.0)       # market prices more risk than realized (VRP present)
C['D_r_iv_H'] = np.where(C.ls_iv <= C.ls_r, C.D_r_iv, 0.0)      # realized riskier than the market prices
C['D_cert_iv'] = -np.log(np.clip(Qi[:, 0], 1e-6, 1))
# yesterday's surface at today's strikes
F = pd.concat([pd.read_parquet(f'{HERE}/is_synth.parquet'), pd.read_parquet(f'{HERE}/oot_synth.parquet')])[['ticker', 'entry_date', 'expiry_date', 'c0', 'c1', 'c2', 'sig0']].drop_duplicates(['ticker', 'entry_date', 'expiry_date'])
F['entry_date'] = pd.to_datetime(F.entry_date); F['expiry_date'] = pd.to_datetime(F.expiry_date); F = F.sort_values(['ticker', 'expiry_date', 'entry_date'])
for c in ('c0', 'c1', 'c2', 'sig0', 'entry_date'): F['y_' + c] = F.groupby(['ticker', 'expiry_date'])[c].shift(1)
F = F[(F.entry_date - F.y_entry_date).dt.days <= 4]
C = C.merge(F[['ticker', 'entry_date', 'expiry_date', 'y_c0', 'y_c1', 'y_c2', 'y_sig0']], on=['ticker', 'entry_date', 'expiry_date'], how='left')
T = np.clip(C.DTE.values, 1, None) / 365.0; S = C.entry_price.values
def iv_at(cc0, cc1, cc2, ss, K):
    y = np.clip(np.log(K / S) / np.sqrt(T) / ss, -Y_MAX, Y_MAX); return np.clip(cc0 + cc1 * y + cc2 * y * y, IV_LO, IV_HI)
Qy = iv_triple(C, np.nan_to_num(iv_at(C.y_c0.values, C.y_c1.values, C.y_c2.values, C.y_sig0.values, C.short_strike.values), nan=-1), np.nan_to_num(iv_at(C.y_c0.values, C.y_c1.values, C.y_c2.values, C.y_sig0.values, C.long_strike.values), nan=-1))
oky = np.isfinite(Qy).all(1); ls_y = Qy[:, 1] + 0.5 * Qy[:, 2]
C['D_jump'] = np.where(oky, kl(Qi, np.nan_to_num(Qy, nan=1/3)), 0.0)
C['D_jump_up'] = np.where(oky & (C.ls_iv.values > ls_y), C.D_jump, 0.0); C['D_jump_dn'] = np.where(oky & (C.ls_iv.values < ls_y), C.D_jump, 0.0)
C['ivchg'] = np.where(oky, np.log(C.sig0 / C.y_sig0), np.nan)
# front vs next expiry
Tm = pd.read_parquet(f'{HERE}/term_atm.parquet').rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date'}); Tm['entry_date'] = pd.to_datetime(Tm.entry_date); Tm = Tm[Tm.n >= 4]
fr = Tm.rename(columns={'ExpirationDate': 'expiry_date', 'atm_iv': 'atm_front'}); fr['expiry_date'] = pd.to_datetime(fr.expiry_date)
C = C.merge(fr[['ticker', 'entry_date', 'expiry_date', 'atm_front']], on=['ticker', 'entry_date', 'expiry_date'], how='left')
bk = Tm.rename(columns={'atm_iv': 'atm_back', 'DTE': 'dte_back'})[['ticker', 'entry_date', 'dte_back', 'atm_back']]
key = ['ticker', 'entry_date', 'expiry_date', 'spread_type', 'short_strike']
B = C[key + ['DTE']].merge(bk, on=['ticker', 'entry_date'], how='left'); B = B[(B.dte_back > B.DTE) & (B.dte_back <= B.DTE + 14)].sort_values('dte_back').drop_duplicates(key)
C = C.merge(B[key + ['atm_back']], on=key, how='left')
ratio = (C.atm_front / C.atm_back).values
Qb = iv_triple(C, np.nan_to_num(C.iv_fit_short.values / ratio, nan=-1), np.nan_to_num(C.iv_fit_long.values / ratio, nan=-1)); okb = np.isfinite(Qb).all(1); ls_b = Qb[:, 1] + 0.5 * Qb[:, 2]
C['D_term'] = np.where(okb, kl(Qi, np.nan_to_num(Qb, nan=1/3)), 0.0)
C['D_term_rich'] = np.where(okb & (C.ls_iv.values > ls_b), C.D_term, 0.0); C['D_term_cheap'] = np.where(okb & (C.ls_iv.values < ls_b), C.D_term, 0.0)
print(f'ATM band candidates {len(C):,}; valid G {C.EV.notna().mean():.1%}; yesterday coverage {oky.mean():.0%}; back-expiry coverage {okb.mean():.0%}')
print(f'market riskier than realized on {(C.ls_iv > C.ls_r).mean():.1%}; median D_r_iv {C.D_r_iv.median():.4f}, D_jump {C.D_jump[C.D_jump>0].median():.4f}, D_term {C.D_term[C.D_term>0].median():.4f}')
ISc, OOc = C[C.win == 'IS'].dropna(subset=['EV']), C[C.win == 'OOT'].dropna(subset=['EV'])
de.FILL, de.THR = 1.08, 0.01
def sel(df, k, dc):
    d = df.copy(); d['GR'] = d['EV'] * np.exp(k * d[dc]); d = d[d.GR >= de.THR]
    return d.sort_values(['entry_date', 'GR'], ascending=[True, False]).groupby('entry_date').head(5)
def qt(df, col):
    d = df[df[col] > 0] if (df[col] > 0).mean() < 0.9 else df
    q = pd.qcut(d[col].rank(method='first'), 5, labels=False); g = d.groupby(q).agg(loss=('is_loss', 'mean') if 'is_loss' in d else ('outcome', lambda s: (s == 'LOSS').mean()), EV=('EV', 'mean'))
    return f"n={len(d)} loss {g.loss.round(3).tolist()} EV {g.EV.round(4).tolist()}"
for c in (ISc, OOc): c['is_loss'] = (c.outcome == 'LOSS').astype(float)
KS = (-16, -8, -4, -2, 0, 2, 4, 8, 16)
for dc in ('D_cert_iv', 'D_jump', 'D_jump_up', 'D_jump_dn', 'D_term', 'D_term_rich', 'D_term_cheap'):
    print(f'\n=== 50-60 delta, DKL = {dc}  (k<0 = discount exp(-|k|D), k>0 = reward) ===\n IS : {qt(ISc, dc)}\n OOT: {qt(OOc, dc)}')
    rows = []
    for k in KS:
        a = book_comm(sel(ISc, k, dc), pd.Timestamp('2025-12-31'), 1.30); o = book_comm(sel(OOc, k, dc), pd.Timestamp('2026-08-31'), 1.30)
        rows.append(dict(k=k, is_n=a['n'], is_fin=round(a['final']), is_sh=round(a['sharpe'], 2), is_dd=round(a['dd'], 1), worst=round(a['worst_wk'], 1), oot_n=o['n'], oot_fin=round(o['final']), oot_sh=round(o['sharpe'], 2), oot_dd=round(o['dd'], 1)))
    print(pd.DataFrame(rows).to_string(index=False))
C.to_parquet(f'{HERE}/featATM.parquet')
