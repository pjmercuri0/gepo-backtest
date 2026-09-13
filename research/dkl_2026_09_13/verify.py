import sys, time
import numpy as np, pandas as pd
sys.path.insert(0, '/private/tmp/claude-501/-Users-mercurio-Downloads-gepo-backtest/74cb077b-eb69-4075-bfe1-766ea8fc2c15/scratchpad')
import dkl_eval as de
from dkl_eval import *
T0 = time.time(); pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
IS = pd.read_parquet(f'{SCR}/feat2_IS.parquet'); OO = pd.read_parquet(f'{SCR}/feat2_OOT.parquet')
KS = (0, 2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 28, 32, 40)

def sweep2(IS, OO, dcol, score='EV', thr=THR, fill=FILL, ks=KS):
    de.THR = thr; de.FILL = fill
    r = sweep(IS, OO, dcol, score, ks); de.THR = THR; de.FILL = FILL
    return r

for dc in ['D_emp_iv', 'D_emp_iv_S', 'D_emp_d']:
    print(f'\n===== {dc}: WITH threshold 0.05 (canon) =====')
    print(sweep2(IS, OO, dc).to_string(index=False, float_format=fmt))
    print(f'\n===== {dc}: PURE RE-RANKING, thr=0 (always 5/day) =====')
    print(sweep2(IS, OO, dc, thr=0.0).to_string(index=False, float_format=fmt))
print('\n===== D_emp_iv on shrunk-G base (EV_post), thr 0.05 =====')
print(sweep2(IS, OO, 'D_emp_iv', score='EV_post').to_string(index=False, float_format=fmt))

# ---- risk monotonicity holding edge fixed ----
def cond_mono(C, dc, label):
    d = C.dropna(subset=['EV']).copy()
    d['evq'] = pd.qcut(d.EV.rank(method='first'), 5, labels=False)
    d['dq'] = d.groupby('evq')[dc].transform(lambda s: pd.qcut(s.rank(method='first'), 5, labels=False))
    t = d.pivot_table(index='evq', columns='dq', values='is_loss', aggfunc='mean').round(4)
    t.columns = [f'D_q{i+1}' for i in t.columns]; t.index = [f'EV_q{i+1}' for i in t.index]
    print(f'\n--- {label}: LOSS rate by {dc} quintile WITHIN EV quintile ---'); print(t.to_string())
    top = d.sort_values(['entry_date', 'EV'], ascending=[True, False]).groupby('entry_date').head(20)
    q = pd.qcut(top[dc].rank(method='first'), 5, labels=False)
    g = top.groupby(q).agg(loss=('is_loss', 'mean'), ls_real=('loss_share', 'mean'), ls_pred=('pred_loss_share', 'mean'), EV=('EV', 'mean'), D=(dc, 'mean'))
    print(f'--- {label}: top-20/day by EV, by {dc} quintile ---'); print(g.round(4).T.to_string())
for dc in ['D_emp_iv', 'D_emp_d']:
    cond_mono(IS, dc, 'IS'); cond_mono(OO, dc, 'OOT')

# ---- year by year, k=0 vs k=12 vs k=16 ----
print('\n===== D_emp_iv year-by-year (IS), thr 0.05 =====')
rows = []
for y in range(2020, 2026):
    sub = IS[IS.entry_date.dt.year == y]
    for k in (0, 8, 12, 16, 24):
        b = book(select(sub.dropna(subset=['EV']), 'EV', k, 'D_emp_iv'), pd.Timestamp(f'{y}-12-31'))
        rows.append(dict(year=y, k=k, n=b['n'], final=b['final'], sharpe=b['sharpe'], dd=b['dd'], loss=b['loss']))
R = pd.DataFrame(rows)
print(R.pivot(index='year', columns='k', values='sharpe').round(2).to_string())
print(R.pivot(index='year', columns='k', values='final').round(0).to_string())
print(R.pivot(index='year', columns='k', values='n').to_string())

# ---- fill stress ----
print('\n===== D_emp_iv fill stress (credit = f x mid) =====')
for f in (0.90, 0.80, 0.70, 0.60):
    r = sweep2(IS, OO, 'D_emp_iv', fill=f, ks=(0, 8, 12, 16, 24))
    r.insert(0, 'fill', f); print(r.to_string(index=False, float_format=fmt))

# ---- direction split at k=16 ----
for k in (0, 16):
    s = select(IS.dropna(subset=['EV']), 'EV', k, 'D_emp_iv')
    print(f'\nk={k}: direction mix {s.spread_type.value_counts(normalize=True).round(3).to_dict()}, '
          f'med d_sh {s.d_sh.median():.3f}, med b {s.b.median():.3f}, med otm {s.otm.median():.2f}%, '
          f'med IV {s.IV.median():.3f}, med D {s.D_emp_iv.median():.4f}, trades/day {len(s)/s.entry_date.nunique():.2f}, days {s.entry_date.nunique()}')
print(f'DONE {time.time()-T0:.0f}s')
