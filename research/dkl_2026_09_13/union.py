"""Reference = the market's most pessimistic prediction for THIS spread among its own views
(smile, flat ATM, mirrored wing, yesterday's surface, next expiry level).  D_max = D(Q_ref || Q_smile), fires when
today's price for this spread is below the market's richest estimate of its risk."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, select, kl
from diag import iv_triple
from synth_credit import Y_MAX, IV_LO, IV_HI
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
C = pd.read_parquet(f'{HERE}/featM4.parquet'); C['entry_date'] = pd.to_datetime(C.entry_date); C['expiry_date'] = pd.to_datetime(C.expiry_date); C['Z'] = 0.0
Tm = pd.read_parquet(f'{HERE}/term_atm.parquet').rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date'}); Tm['entry_date'] = pd.to_datetime(Tm.entry_date)
Tm = Tm[Tm.n >= 4]
fr = Tm.rename(columns={'ExpirationDate': 'expiry_date', 'atm_iv': 'atm_front'}); fr['expiry_date'] = pd.to_datetime(fr.expiry_date)
C = C.merge(fr[['ticker', 'entry_date', 'expiry_date', 'atm_front']], on=['ticker', 'entry_date', 'expiry_date'], how='left')
bk = Tm.rename(columns={'atm_iv': 'atm_back', 'DTE': 'dte_back'})[['ticker', 'entry_date', 'dte_back', 'atm_back']]
key = ['ticker', 'entry_date', 'expiry_date', 'spread_type', 'short_strike']
B = C[key + ['DTE']].merge(bk, on=['ticker', 'entry_date'], how='left'); B = B[(B.dte_back > B.DTE) & (B.dte_back <= B.DTE + 14)].sort_values('dte_back').drop_duplicates(key)
C = C.merge(B[key + ['atm_back']], on=key, how='left')
T = np.clip(C.DTE.values, 1, None) / 365.0; S = C.entry_price.values
def iv_at(cc0, cc1, cc2, ss, K, sign=1.0):
    y = np.clip(np.log(K / S) / np.sqrt(T) / ss, -Y_MAX, Y_MAX); return np.clip(cc0 + sign * cc1 * y + cc2 * y * y, IV_LO, IV_HI)
Qi = C[['p_iv', 'q_iv', 'ro_iv']].values
views = {'flat': iv_triple(C, C.sig0.values, C.sig0.values),
         'mirror': iv_triple(C, iv_at(C.c0.values, C.c1.values, C.c2.values, C.sig0.values, C.short_strike.values, -1), iv_at(C.c0.values, C.c1.values, C.c2.values, C.sig0.values, C.long_strike.values, -1)),
         'yest': iv_triple(C, np.nan_to_num(iv_at(C.y_c0.values, C.y_c1.values, C.y_c2.values, C.y_sig0.values, C.short_strike.values), nan=-1), np.nan_to_num(iv_at(C.y_c0.values, C.y_c1.values, C.y_c2.values, C.y_sig0.values, C.long_strike.values), nan=-1))}
ratio = (C.atm_front / C.atm_back).values
views['back'] = iv_triple(C, np.nan_to_num(C.iv_fit_short.values / ratio, nan=-1), np.nan_to_num(C.iv_fit_long.values / ratio, nan=-1))
ls = {k: v[:, 1] + 0.5 * v[:, 2] for k, v in views.items()}
LS = np.column_stack([np.nan_to_num(ls[k], nan=-1) for k in views]); which = LS.argmax(1); ls_ref = LS.max(1)
Qref = np.stack([views[k] for k in views], 0)[which, np.arange(len(C))]
cheap = ls_ref > C.ls_iv.values
C['D_max'] = np.where(cheap, kl(Qref, Qi), 0.0); C['D_max_r'] = np.where(cheap, kl(Qi, Qref), 0.0)
C['ref_gap'] = ls_ref - C.ls_iv.values; C['ref_view'] = np.array(list(views))[which]
print(f'fires on {cheap.mean():.1%}; median nonzero D_max {C.D_max[C.D_max>0].median():.4f}; reference view mix {pd.Series(C.ref_view[cheap]).value_counts(normalize=True).round(2).to_dict()}')
ISc, OOc = C[C.win == 'IS'], C[C.win == 'OOT']
def qt(df, col, score, bel, positive_only=True):
    d = df.dropna(subset=[col, score]); d = d[d[col] > 0] if positive_only else d
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
    print(f'\n##### {lab} #####'); print(' ref_gap (all) IS :', qt(ISc, 'ref_gap', score, bel, False)); print(' ref_gap (all) OOT:', qt(OOc, 'ref_gap', score, bel, False))
    for dc in ('D_max', 'D_max_r'):
        print(f'\n=== {lab}, DKL = {dc} ===\n IS : {qt(ISc, dc, score, bel)}\n OOT: {qt(OOc, dc, score, bel)}')
        for fill, thr in ((1.1, 0.01), (1.0, 0.01)):
            print(f' -- fill {fill} thr {thr} --'); print(sw(dc, score, KS, fill, thr).to_string(index=False, float_format=fmt))
# equal-count control for P_emp, D_max, fill 1.1
de.FILL = 1.1
for k in (4, 8, 16):
    de.THR = 0.01; a = book(select(ISc.dropna(subset=['EV']), 'EV', k, 'D_max'), pd.Timestamp('2025-12-31')); o = book(select(OOc.dropna(subset=['EV']), 'EV', k, 'D_max'), pd.Timestamp('2026-08-31'))
    lo, hi = 0.01, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2; de.THR = mid; n0 = book(select(ISc.dropna(subset=['EV']), 'EV', 0, 'Z'), pd.Timestamp('2025-12-31'))['n']
        if n0 > a['n']: lo = mid
        else: hi = mid
    de.THR = (lo + hi) / 2; b0 = book(select(ISc.dropna(subset=['EV']), 'EV', 0, 'Z'), pd.Timestamp('2025-12-31')); o0 = book(select(OOc.dropna(subset=['EV']), 'EV', 0, 'Z'), pd.Timestamp('2026-08-31'))
    print(f"\nCONTROL k={k:<3} D_max thr .01: IS n={a['n']} ${a['final']:,.0f} Sh {a['sharpe']:.2f} DD {a['dd']:.1f}% | OOT n={o['n']} Sh {o['sharpe']:.2f} DD {o['dd']:.1f}%\n        k=0   thr {de.THR:.3f}: IS n={b0['n']} ${b0['final']:,.0f} Sh {b0['sharpe']:.2f} DD {b0['dd']:.1f}% | OOT n={o0['n']} Sh {o0['sharpe']:.2f} DD {o0['dd']:.1f}%")
C.to_parquet(f'{HERE}/featU.parquet')
