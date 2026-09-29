"""Bear sleeve gate ablation on the 2026-09-29 canon (bull thr 0.003, no bull parity/regime). Bear: regime below_100, parity > 0.25, GROUND >= 0.001, cap 5."""
import sys, io, contextlib, warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); sys.path.insert(0, 'research'); sys.path.insert(0, 'research/p_real_demean_2026_09_28')
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
S = sys.argv[1]
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
bull = realize(c[c.spread_type.eq('bull_put') & ~c.earnings_hit & ~c.exdiv_hit & (c.GROUND >= ec.THR)], ec.TOP_N)
base = c.spread_type.eq('bear_call') & ~c.exdiv_hit & ~c.earnings_hit
V = {'canon bear (regime, parity>0.25, G>=0.001)': base & c[rbr.REGIME] & (c.bear_parity_pct > rbr.PARITY) & (c.GROUND >= rbr.GROUND),
     'bear - parity': base & c[rbr.REGIME] & (c.GROUND >= rbr.GROUND),
     'bear parity>0.12': base & c[rbr.REGIME] & (c.bear_parity_pct > 0.12) & (c.GROUND >= rbr.GROUND),
     'bear parity>0.50': base & c[rbr.REGIME] & (c.bear_parity_pct > 0.50) & (c.GROUND >= rbr.GROUND),
     'bear - regime (any day)': base & (c.bear_parity_pct > rbr.PARITY) & (c.GROUND >= rbr.GROUND),
     'no bear sleeve': None}
rows = []
for name, m in V.items():
    parts = [bull] + ([realize(c[m], rbr.CAP)] if m is not None else [])
    p = rbr.enrich(pd.concat(parts, ignore_index=True))
    for win, ey, wm in (('IS', 2025, p.entry_date.dt.year <= 2025), ('OOT', 2026, p.entry_date.dt.year == 2026)):
        q = p[wm].copy(); qb = q[q.spread_type.eq('bear_call')]
        with contextlib.redirect_stdout(io.StringIO()):
            s = rmc.build_payload(q, ey, '')['summary']
        rows.append(dict(variant=name, win=win, n=s['n_trades'], bear_n=len(qb), bear_pnl=round(qb.pnl_per_contract.sum()), bear_win=round((qb._outcome=='WIN').mean()*100,1) if len(qb) else None,
                         bear_loss=round((qb._outcome=='LOSS').mean()*100,1) if len(qb) else None, final=s['strategy_final'], sh_dollar=s['strategy_sharpe_dollar'], max_dd=s['strategy_max_dd'], yield_pct=s['strategy_yield']))
    print(name, 'done', flush=True)
R = pd.DataFrame(rows); pd.set_option('display.width', 250); print(R.to_string(index=False)); R.to_csv(f'{S}/bear_ablate.csv', index=False); print('ALLDONE')
