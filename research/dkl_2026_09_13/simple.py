"""SIMPLE: D = D( Q(IV at the strikes) || Q(ATM IV) ), raw vendor IVs, no smile fit.  Reward form GROUND = EV * exp(+k*D).
Fires when the wing is priced above ATM vol.  G on the 52:10 empirical belief.  Credit = smile-fit fill price."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, kl
from diag import iv_triple
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
C = pd.read_parquet(f'{HERE}/featRW.parquet'); C['entry_date'] = pd.to_datetime(C.entry_date); C['expiry_date'] = pd.to_datetime(C.expiry_date); C['Z'] = 0.0
key = ['ticker', 'entry_date', 'expiry_date', 'spread_type', 'short_strike']
R = pd.concat([pd.read_parquet(f'{HERE}/is_resel.parquet'), pd.read_parquet(f'{HERE}/oot_resel.parquet')])[key + ['IV', 'long_IV', 'sig0']].rename(columns={'IV': 'ivs_raw', 'long_IV': 'ivl_raw', 'sig0': 'atm_raw'})
R['entry_date'] = pd.to_datetime(R.entry_date); R['expiry_date'] = pd.to_datetime(R.expiry_date)
C = C.merge(R.drop_duplicates(key), on=key, how='left')
Qw = iv_triple(C, C.ivs_raw.fillna(-1).values, C.ivl_raw.fillna(C.ivs_raw).fillna(-1).values)     # wing priced at its OWN raw IVs
Qa = iv_triple(C, C.atm_raw.fillna(-1).values, C.atm_raw.fillna(-1).values)                        # same strikes at ATM IV
ok = np.isfinite(Qw).all(1) & np.isfinite(Qa).all(1)
lsw = Qw[:, 1] + 0.5 * Qw[:, 2]; lsa = Qa[:, 1] + 0.5 * Qa[:, 2]
C['D_simple'] = np.where(ok & (lsw > lsa), kl(np.nan_to_num(Qw, nan=1/3), np.nan_to_num(Qa, nan=1/3)), 0.0)
C['iv_ratio'] = C.ivs_raw / C.atm_raw
print(f'coverage {ok.mean():.1%}; wing above ATM on {(C.D_simple>0).mean():.1%}; median nonzero D {C.D_simple[C.D_simple>0].median():.4f}; median IV_short/IV_atm {C.iv_ratio.median():.3f}')
ISc, OOc = C[C.win == 'IS'], C[C.win == 'OOT']
def select_reward(df, score, k, dc):
    d = df.copy(); d['GR'] = d[score] * np.exp(+k * d[dc]); d = d[d.GR >= de.THR]
    return d.sort_values(['entry_date', 'GR'], ascending=[True, False]).groupby('entry_date').head(5)
for lab, sub in (('IS', ISc), ('OOT', OOc)):
    d = sub[(sub.D_simple > 0) & sub.EV.notna()]; q = pd.qcut(d.D_simple.rank(method='first'), 5, labels=False)
    g = d.groupby(q).agg(loss=('is_loss', 'mean'), ex=('loss_share', 'mean'), b=('pred_loss_share', 'mean'), EV=('EV', 'mean'))
    print(f'{lab}: loss by D quintile {g.loss.round(3).tolist()}  realized-minus-belief {(g.ex-g.b).round(3).tolist()}  EV {g.EV.round(4).tolist()}')
for fill in (1.1, 1.0):
    de.FILL, de.THR = fill, 0.01; rows = []
    for k in (0, 2, 4, 8, 12, 16, 24, 32, 64):
        a = book(select_reward(ISc.dropna(subset=['EV']), 'EV', k, 'D_simple'), pd.Timestamp('2025-12-31')); o = book(select_reward(OOc.dropna(subset=['EV']), 'EV', k, 'D_simple'), pd.Timestamp('2026-08-31'))
        rows.append(dict(k=k, is_n=a['n'], is_fin=a['final'], is_sh=a['sharpe'], is_dd=a['dd'], is_loss=a['loss'], oot_n=o['n'], oot_fin=o['final'], oot_sh=o['sharpe'], oot_dd=o['dd'], oot_loss=o['loss']))
    print(f'\n=== SIMPLE reward, G on P_emp 52:10, fill {fill}x model, thr 0.01 ==='); print(pd.DataFrame(rows).to_string(index=False, float_format=fmt))
de.FILL, de.THR = 1.1, 0.01
print('\nyear by year Sharpe, k=0 / 8 / 16:')
for y in range(2020, 2027):
    sub = C[C.entry_date.dt.year == y].dropna(subset=['EV']); end = pd.Timestamp(f'{y}-12-31') if y < 2026 else pd.Timestamp('2026-08-31')
    print(f'  {y}: ' + '  '.join(f"k={k} {book(select_reward(sub, 'EV', k, 'D_simple'), end)['sharpe']:.2f}" for k in (0, 8, 16)))
a = book(select_reward(ISc.dropna(subset=['EV']), 'EV', 8, 'D_simple'), pd.Timestamp('2025-12-31'))
lo, hi = 0.0, 0.01
for _ in range(40):
    mid = (lo + hi) / 2; de.THR = mid; n0 = book(select_reward(ISc.dropna(subset=['EV']), 'EV', 0, 'Z'), pd.Timestamp('2025-12-31'))['n']
    if n0 > a['n']: lo = mid
    else: hi = mid
de.THR = (lo + hi) / 2; b0 = book(select_reward(ISc.dropna(subset=['EV']), 'EV', 0, 'Z'), pd.Timestamp('2025-12-31')); o0 = book(select_reward(OOc.dropna(subset=['EV']), 'EV', 0, 'Z'), pd.Timestamp('2026-08-31'))
de.THR = 0.01; o = book(select_reward(OOc.dropna(subset=['EV']), 'EV', 8, 'D_simple'), pd.Timestamp('2026-08-31'))
print(f"\nequal-count control: k=8 IS Sh {a['sharpe']:.2f} DD {a['dd']:.1f}% | OOT Sh {o['sharpe']:.2f} DD {o['dd']:.1f}%   vs  k=0 matched IS Sh {b0['sharpe']:.2f} DD {b0['dd']:.1f}% | OOT Sh {o0['sharpe']:.2f} DD {o0['dd']:.1f}%")
C.to_parquet(f'{HERE}/featSimple.parquet')
