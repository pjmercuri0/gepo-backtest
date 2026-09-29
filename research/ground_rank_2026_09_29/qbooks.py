import sys, io, contextlib, warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); sys.path.insert(0, 'research'); import ent_canon as ec, report_mid_canon as rmc
S = sys.argv[1]; r = pd.read_parquet(f'{S}/bull_pool.parquet'); r['yr'] = r.entry_date.dt.year
r['b'] = r.model_credit/(r.width-r.model_credit)
print('corr with D_ent:', r[['D_ent','q_win','q_loss','q_part','b','dfit_short','dist','width','entry_price','q','p']].corr().D_ent.round(3).to_dict())
dq = pd.qcut(r.D_ent.rank(method='first'), 5, labels=False)
print('by D_ent quintile:'); print(r.groupby(dq)[['D_ent','q_win','q_loss','q_part','b','dfit_short','dist','q']].mean().round(3).to_string())
rows = []
for key in ('GROUND', 'EV', 'D_ent'):
    for win, wm, ey in (('IS', r.yr <= 2025, 2025), ('OOT', r.yr == 2026, 2026)):
        sub = r[wm].copy(); qq = pd.qcut(sub[key].rank(method='first'), 5, labels=False)
        for qi in range(5):
            q = sub[qq == qi].copy()
            with contextlib.redirect_stdout(io.StringIO()):
                s = rmc.build_payload(q, ey, '')['summary']
            rows.append(dict(score=key, win=win, quintile=qi+1, n=len(q), lo=round(q[key].min(),4), hi=round(q[key].max(),4),
                             sh_dollar=s['strategy_sharpe_dollar'], max_dd=s['strategy_max_dd'], yield_pct=s['strategy_yield'],
                             win_pct=round((q._outcome=='WIN').mean()*100,1), loss_pct=round((q._outcome=='LOSS').mean()*100,1),
                             mean_b=round(q.b.mean(),3), pnl_per_trade=round(q.pnl_per_contract.mean(),2)))
        print(key, win, 'done', flush=True)
R = pd.DataFrame(rows); pd.set_option('display.width', 250); print(R.to_string(index=False)); R.to_csv(f'{S}/quintile_books.csv', index=False)
