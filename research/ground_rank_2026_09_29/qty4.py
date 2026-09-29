"""thr=0.003 -parity -regime at qty4 (rows doubled) vs thr=0.0005 qty2: does sizing up recover the dollars?"""
import sys, io, contextlib, warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); sys.path.insert(0, 'research'); sys.path.insert(0, 'research/p_real_demean_2026_09_28')
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
S = sys.argv[1]
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
BEAR = c[c.spread_type.eq('bear_call') & ~c.exdiv_hit & ~c.earnings_hit & c[rbr.REGIME] & (c.bear_parity_pct > rbr.PARITY) & (c.GROUND >= rbr.GROUND)]
POOL = c[c.spread_type.eq('bull_put') & ~c.earnings_hit & ~c.exdiv_hit]
bear = realize(BEAR, rbr.CAP); rows = []
for thr, rep in ((0.0005, 1), (0.003, 1), (0.003, 2), (0.0025, 2), (0.0035, 2)):
    bull = realize(POOL[POOL.GROUND >= thr], 10)
    p = rbr.enrich(pd.concat([bull.loc[bull.index.repeat(rep)], bear.loc[bear.index.repeat(rep)]], ignore_index=True))
    for win, ey, m in (('IS', 2025, p.entry_date.dt.year <= 2025), ('OOT', 2026, p.entry_date.dt.year == 2026)):
        q = p[m]
        with contextlib.redirect_stdout(io.StringIO()):
            s = rmc.build_payload(q, ey, '')['summary']
        rows.append(dict(thr=thr, qty=2*rep, win=win, n=len(q)//rep, final=s['strategy_final'], sh_dollar=s['strategy_sharpe_dollar'], max_dd=s['strategy_max_dd'], wagered=s['strategy_wagered'], yield_pct=s['strategy_yield']))
    print(thr, rep, 'done', flush=True)
R = pd.DataFrame(rows); pd.set_option('display.width', 250); print(R.to_string(index=False)); R.to_csv(f'{S}/qty4.csv', index=False); print('ALLDONE')
