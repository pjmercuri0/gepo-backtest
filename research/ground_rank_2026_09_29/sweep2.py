"""-parity -regime canon: GROUND threshold x cap x tiered sizing. Tiers: qty multiplier by GROUND rank among qualified picks."""
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
bear = realize(BEAR, rbr.CAP)
def metrics(p, name):
    out = []
    for win, ey, m in (('IS', 2025, p.entry_date.dt.year <= 2025), ('OOT', 2026, p.entry_date.dt.year == 2026)):
        q = p[m].copy()
        with contextlib.redirect_stdout(io.StringIO()):
            s = rmc.build_payload(q, ey, '')['summary']
        out.append(dict(variant=name, win=win, n=s['n_trades'], final=s['strategy_final'], sh_dollar=s['strategy_sharpe_dollar'], max_dd=s['strategy_max_dd'],
                        yield_pct=s['strategy_yield'], win_pct=round((q._outcome=='WIN').mean()*100,1), loss_pct=round((q._outcome=='LOSS').mean()*100,1)))
    return out
rows = []
for thr in (0.0005, 0.001, 0.002, 0.003, 0.005):
    for cap in (5, 10, 20, 1000):
        bull = realize(POOL[POOL.GROUND >= thr], cap)
        p = rbr.enrich(pd.concat([bull, bear], ignore_index=True)); rows += metrics(p, f'thr={thr} cap={cap}')
        print(thr, cap, 'done', flush=True)
        pd.DataFrame(rows).to_csv(f'{S}/sweep2.csv', index=False)
# tiered sizing: qualified (thr 0.0005, cap 10) picks, qty x1 / x2 / x3 by GROUND tercile of the IS qualified set (cuts fixed from IS)
bull = realize(POOL[POOL.GROUND >= 0.0005], 10)
cuts = bull[bull.entry_date.dt.year <= 2025].GROUND.quantile([1/3, 2/3]).values
tier = 1 + (bull.GROUND >= cuts[0]).astype(int) + (bull.GROUND >= cuts[1]).astype(int)
for name, mult in (('tiered 1/2/3', {1:1, 2:2, 3:3}), ('tiered 0/1/2 (drop bottom tercile)', {1:0, 2:1, 3:2}), ('top tercile only x1', {1:0, 2:0, 3:1})):
    parts = [bull[tier == k].loc[bull[tier == k].index.repeat(mult[k])] for k in (1, 2, 3) if mult[k] > 0]
    p = rbr.enrich(pd.concat(parts + [bear], ignore_index=True)); rows += metrics(p, name + f' (cuts {cuts[0]:.4f},{cuts[1]:.4f})')
    print(name, 'done', flush=True)
R = pd.DataFrame(rows); R.to_csv(f'{S}/sweep2.csv', index=False); pd.set_option('display.width', 250); print(R.to_string(index=False))
