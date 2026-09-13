"""REWARD form: GROUND = EV * exp(+k * D).  D = D(Q_smile || Q_min), Q_min = the market's most OPTIMISTIC view of this
spread among {flat ATM, mirrored wing, yesterday's surface, next expiry}; fires when this wing is priced richer than it.
Also individual rich-signed forms: vs flat (skew), vs mirror, vs yesterday."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, kl
from diag import iv_triple
from synth_credit import Y_MAX, IV_LO, IV_HI
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
C = pd.read_parquet(f'{HERE}/featU.parquet'); C['entry_date'] = pd.to_datetime(C.entry_date); C['Z'] = 0.0
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
LS = np.column_stack([np.nan_to_num(ls[k], nan=9) for k in views]); which = LS.argmin(1); ls_min = LS.min(1)
Qmin = np.stack([views[k] for k in views], 0)[which, np.arange(len(C))]
rich = C.ls_iv.values > ls_min
C['D_rich'] = np.where(rich, kl(Qi, Qmin), 0.0)
C['D_rich_flat'] = np.where(C.ls_iv.values > ls['flat'], kl(Qi, views['flat']), 0.0)
C['D_rich_mirror'] = np.where(C.ls_iv.values > ls['mirror'], kl(Qi, views['mirror']), 0.0)
okY = np.isfinite(ls['yest']); C['D_rich_yest'] = np.where(okY & (C.ls_iv.values > np.nan_to_num(ls['yest'], nan=9)), kl(Qi, np.nan_to_num(views['yest'], nan=1/3)), 0.0)
print(f'rich vs the market\'s cheapest view on {rich.mean():.1%}; median nonzero D_rich {C.D_rich[C.D_rich>0].median():.4f}; cheapest view mix {pd.Series(np.array(list(views))[which][rich]).value_counts(normalize=True).round(2).to_dict()}')
ISc, OOc = C[C.win == 'IS'], C[C.win == 'OOT']
def select_reward(df, score, k, dc):
    d = df.copy(); d['GR'] = d[score] * np.exp(+k * d[dc]); d = d[d.GR >= de.THR]
    return d.sort_values(['entry_date', 'GR'], ascending=[True, False]).groupby('entry_date').head(5)
def qt(df, col, score, bel):
    d = df.dropna(subset=[col, score]); d = d[d[col] > 0]; q = pd.qcut(d[col].rank(method='first'), 5, labels=False)
    g = d.groupby(q).agg(loss=('is_loss', 'mean'), real=('loss_share', 'mean'), b=(bel, 'mean'), EV=(score, 'mean'))
    return f"n={len(d)} loss {g.loss.round(3).tolist()} realized-minus-belief {(g.real-g.b).round(3).tolist()} EV {g.EV.round(4).tolist()}"
def sw(dc, score, ks, fill, thr):
    de.FILL, de.THR = fill, thr; rows = []
    for k in ks:
        a = book(select_reward(ISc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2025-12-31')); o = book(select_reward(OOc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2026-08-31'))
        rows.append(dict(k=k, is_n=a['n'], is_fin=a['final'], is_sh=a['sharpe'], is_dd=a['dd'], is_loss=a['loss'], oot_n=o['n'], oot_fin=o['final'], oot_sh=o['sharpe'], oot_dd=o['dd'], oot_loss=o['loss']))
    return pd.DataFrame(rows)
KS = (0, 2, 4, 8, 16, 32, 64, 128)
for score, bel, lab in (('EV', 'pred_loss_share', 'G on P_emp 52:10'), ('EV_r', 'ls_r', 'G on P_real')):
    for dc in ('D_rich', 'D_rich_flat', 'D_rich_mirror', 'D_rich_yest'):
        print(f'\n=== REWARD exp(+k*D): {lab}, D = {dc} ===\n IS : {qt(ISc, dc, score, bel)}\n OOT: {qt(OOc, dc, score, bel)}')
        for fill, thr in ((1.1, 0.01), (1.0, 0.01)):
            print(f' -- fill {fill} thr {thr} --'); print(sw(dc, score, KS, fill, thr).to_string(index=False, float_format=fmt))
# year-by-year + equal-count control for P_emp / D_rich at fill 1.1
de.FILL = 1.1; de.THR = 0.01
print('\nYEAR BY YEAR — G on P_emp, reward D_rich, fill 1.1, thr 0.01 (Sharpe / DD)')
rows = []
for y in range(2020, 2027):
    sub = C[C.entry_date.dt.year == y].dropna(subset=['EV'])
    for k in (0, 8, 16, 32):
        r = book(select_reward(sub, 'EV', k, 'D_rich'), pd.Timestamp(f'{y}-12-31') if y < 2026 else pd.Timestamp('2026-08-31'))
        rows.append(dict(year=y, k=k, sharpe=round(r['sharpe'], 2), dd=round(r['dd'], 1), n=r['n']))
R = pd.DataFrame(rows); print(R.pivot(index='year', columns='k', values='sharpe').to_string()); print(R.pivot(index='year', columns='k', values='dd').to_string())
print('\nEQUAL-TRADE-COUNT CONTROL (reward admits more trades: match by LOWERING the k=0 threshold)')
for k in (8, 16, 32):
    de.THR = 0.01; a = book(select_reward(ISc.dropna(subset=['EV']), 'EV', k, 'D_rich'), pd.Timestamp('2025-12-31')); o = book(select_reward(OOc.dropna(subset=['EV']), 'EV', k, 'D_rich'), pd.Timestamp('2026-08-31'))
    lo, hi = 0.0, 0.01
    for _ in range(40):
        mid = (lo + hi) / 2; de.THR = mid; n0 = book(select_reward(ISc.dropna(subset=['EV']), 'EV', 0, 'Z'), pd.Timestamp('2025-12-31'))['n']
        if n0 > a['n']: lo = mid
        else: hi = mid
    de.THR = (lo + hi) / 2; b0 = book(select_reward(ISc.dropna(subset=['EV']), 'EV', 0, 'Z'), pd.Timestamp('2025-12-31')); o0 = book(select_reward(OOc.dropna(subset=['EV']), 'EV', 0, 'Z'), pd.Timestamp('2026-08-31'))
    print(f"k={k:<3} reward thr .01: IS n={a['n']} ${a['final']:,.0f} Sh {a['sharpe']:.2f} DD {a['dd']:.1f}% loss {a['loss']:.1f}% | OOT n={o['n']} ${o['final']:,.0f} Sh {o['sharpe']:.2f} DD {o['dd']:.1f}%\n      k=0  thr {de.THR:.4f}: IS n={b0['n']} ${b0['final']:,.0f} Sh {b0['sharpe']:.2f} DD {b0['dd']:.1f}% loss {b0['loss']:.1f}% | OOT n={o0['n']} ${o0['final']:,.0f} Sh {o0['sharpe']:.2f} DD {o0['dd']:.1f}%")
C.to_parquet(f'{HERE}/featRW.parquet')
