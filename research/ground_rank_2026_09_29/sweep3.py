"""-parity -regime: fine GROUND threshold grid 0.0015-0.005, cap 10, bear sleeve canon. Per-year qty1 P&L + full summary."""
import sys, io, contextlib, warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); sys.path.insert(0, 'research'); sys.path.insert(0, 'research/p_real_demean_2026_09_28')
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
from demean_lib import trailing_ret
S = sys.argv[1]
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
BEAR = c[c.spread_type.eq('bear_call') & ~c.exdiv_hit & ~c.earnings_hit & c[rbr.REGIME] & (c.bear_parity_pct > rbr.PARITY) & (c.GROUND >= rbr.GROUND)]
POOL = c[c.spread_type.eq('bull_put') & ~c.earnings_hit & ~c.exdiv_hit]
bear = realize(BEAR, rbr.CAP); CLS = ec.backtest_closes()
rows = []; years = {}; full = {}
for thr in (0.0005, 0.0015, 0.002, 0.0025, 0.003, 0.0035, 0.004, 0.0045, 0.005):
    p = rbr.enrich(pd.concat([realize(POOL[POOL.GROUND >= thr], 10), bear], ignore_index=True))
    for win, ey, m in (('IS', 2025, p.entry_date.dt.year <= 2025), ('OOT', 2026, p.entry_date.dt.year == 2026)):
        q = p[m].copy()
        with contextlib.redirect_stdout(io.StringIO()):
            s = dict(rmc.build_payload(q, ey, '')['summary'])
        s.update(win_pct=round((q._outcome=='WIN').mean()*100,1), partial_pct=round((q._outcome=='PARTIAL').mean()*100,1), loss_pct=round((q._outcome=='LOSS').mean()*100,1),
                 bull_n=int(q.spread_type.eq('bull_put').sum()), bear_n=int(q.spread_type.eq('bear_call').sum()), pnl_per_trade=round(q.pnl_per_contract.mean(),2),
                 med_credit=round(q.credit.median(),2), med_1y_ret=round(np.nanmedian(trailing_ret(q, CLS))*100,1), n_tickers=q.ticker.nunique(),
                 trades_per_week=round(len(q)/q.entry_date.dt.to_period('W').nunique(),1))
        full[f'thr={thr} {win}'] = s
        rows.append(dict(thr=thr, win=win, n=s['n_trades'], final=s['strategy_final'], sh_dollar=s['strategy_sharpe_dollar'], max_dd=s['strategy_max_dd'], yield_pct=s['strategy_yield'], loss_pct=s['loss_pct']))
    years[thr] = p.groupby(p.entry_date.dt.year).pnl_per_contract.sum().round(0).astype(int)
    print(thr, 'done', flush=True)
R = pd.DataFrame(rows); pd.set_option('display.width', 250); pd.set_option('display.max_rows', 200)
P = R.pivot_table(index='thr', columns='win', values=['n','final','sh_dollar','max_dd','yield_pct','loss_pct'], sort=False); P.columns=[f'{a}_{b}' for a,b in P.columns]
print(P[['n_IS','final_IS','sh_dollar_IS','max_dd_IS','yield_pct_IS','loss_pct_IS','n_OOT','final_OOT','sh_dollar_OOT','max_dd_OOT','yield_pct_OOT','loss_pct_OOT']].to_string())
print('\nPER YEAR qty1'); print(pd.DataFrame(years).to_string())
print('\nFULL'); F = pd.DataFrame(full); print(F[[k for k in F.columns if k.startswith(('thr=0.0005','thr=0.003 '))]].to_string())
R.to_csv(f'{S}/sweep3.csv', index=False); F.to_csv(f'{S}/sweep3_full.csv'); pd.DataFrame(years).to_csv(f'{S}/sweep3_years.csv')
print('ALLDONE')
