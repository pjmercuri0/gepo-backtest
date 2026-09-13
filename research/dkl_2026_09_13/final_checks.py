import sys, os, time
import numpy as np, pandas as pd
SCR = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, SCR)
import dkl_eval as de
from dkl_eval import *
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
IS = pd.read_parquet(f'{SCR}/feat3_IS.parquet').dropna(subset=['EV']); OO = pd.read_parquet(f'{SCR}/feat3_OOT.parquet').dropna(subset=['EV'])

def run(C, end, dc, k, thr):
    de.THR = thr; s = select(C, 'EV', k, dc); de.THR = THR
    b = book(s, end); b['k'] = k; b['thr'] = thr; return b

# ---- CONTROL: equal trade count. k=0 with a higher EV threshold vs k>0 at thr 0.05 ----
print('===== CONTROL: DKL at thr 0.05 vs k=0 with EV threshold raised to match trade count =====')
for dc, ks in (('D_emp_iv', (8, 12, 16, 24)), ('D_cert_iv', (2, 3, 4, 6))):
    print(f'\n--- {dc} ---')
    for k in ks:
        a = run(IS, pd.Timestamp('2025-12-31'), dc, k, 0.05); ao = run(OO, pd.Timestamp('2026-08-31'), dc, k, 0.05)
        # find thr for k=0 giving ~same IS trade count
        lo, hi = 0.05, 2.0
        for _ in range(40):
            mid = (lo + hi) / 2; n0 = len(select(IS.assign(Z=0.0), 'EV', 0, 'Z').query('GR >= @mid'))
            n0 = book(IS.assign(Z=0.0).pipe(lambda d: d[d.EV >= mid]).sort_values(['entry_date', 'EV'], ascending=[True, False]).groupby('entry_date').head(5), pd.Timestamp('2025-12-31'))['n']
            if n0 > a['n']: lo = mid
            else: hi = mid
        thr0 = (lo + hi) / 2
        b = run(IS, pd.Timestamp('2025-12-31'), 'D_emp_iv', 0, thr0); bo = run(OO, pd.Timestamp('2026-08-31'), 'D_emp_iv', 0, thr0)
        print(f"k={k:>2} thr=0.05 : IS n={a['n']:>5} ${a['final']:>9,.0f} Sh {a['sharpe']:.2f} DD {a['dd']:.1f}% loss {a['loss']:.1f}% | OOT n={ao['n']:>3} ${ao['final']:>7,.0f} Sh {ao['sharpe']:.2f} DD {ao['dd']:.1f}%")
        print(f"k= 0 thr={thr0:.3f}: IS n={b['n']:>5} ${b['final']:>9,.0f} Sh {b['sharpe']:.2f} DD {b['dd']:.1f}% loss {b['loss']:.1f}% | OOT n={bo['n']:>3} ${bo['final']:>7,.0f} Sh {bo['sharpe']:.2f} DD {bo['dd']:.1f}%")

# ---- year by year + fill stress for D_cert_iv ----
print('\n===== D_cert_iv year-by-year (IS) Sharpe / final / n =====')
rows = []
for y in range(2020, 2026):
    sub = IS[IS.entry_date.dt.year == y]
    for k in (0, 2, 3, 4):
        b = book(select(sub, 'EV', k, 'D_cert_iv'), pd.Timestamp(f'{y}-12-31'))
        rows.append(dict(year=y, k=k, n=b['n'], final=b['final'], sharpe=b['sharpe']))
R = pd.DataFrame(rows)
print(R.pivot(index='year', columns='k', values='sharpe').round(2).to_string())
print(R.pivot(index='year', columns='k', values='final').round(0).to_string())
print(R.pivot(index='year', columns='k', values='n').to_string())
print('\n===== D_cert_iv fill stress =====')
for f in (0.90, 0.80, 0.70, 0.60):
    de.FILL = f; r = sweep(IS, OO, 'D_cert_iv', ks=(0, 2, 3, 4)); de.FILL = FILL
    r.insert(0, 'fill', f); print(r.to_string(index=False, float_format=fmt))

# ---- calibration of the selected books ----
print('\n===== selected books: predicted vs realized loss share =====')
for dc, k in (('D_emp_iv', 0), ('D_emp_iv', 16), ('D_cert_iv', 3)):
    s = select(IS, 'EV', k, dc)
    print(f'{dc} k={k}: n={len(s)}, pred loss share (P_emp) {s.pred_loss_share.mean():.4f}, market (Q_iv) {s.ls_iv.mean():.4f}, realized {s.loss_share.mean():.4f}, '
          f'med b {s.b.median():.3f}, med d_sh {s.d_sh.median():.3f}, med IV {s.IV.median():.2f}, mean pnl/ctr {pnl_of(s, (s.net_credit*FILL).round(4).values, (s.width-(s.net_credit*FILL).round(4)).round(4).values).mean():.2f}')
