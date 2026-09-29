"""$10k vs $20k starting wallet, parity/regime off, thr 0.003 (canon) and 0.0005, qty1 and qty2, IS and OOT."""
import sys, io, contextlib, warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); sys.path.insert(0, 'research'); sys.path.insert(0, 'research/p_real_demean_2026_09_28')
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
S = sys.argv[1]
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
BEAR = c[c.spread_type.eq('bear_call') & ~c.exdiv_hit & ~c.earnings_hit & c[rbr.REGIME] & (c.bear_parity_pct > rbr.PARITY) & (c.GROUND >= rbr.GROUND)]
POOL = c[c.spread_type.eq('bull_put') & ~c.earnings_hit & ~c.exdiv_hit]; bear = realize(BEAR, rbr.CAP)
rows = []
for thr in (0.003, 0.0005):
    p = rbr.enrich(pd.concat([realize(POOL[POOL.GROUND >= thr], 10), bear], ignore_index=True))
    for wallet in (10000.0, 20000.0):
        rmc.START_BANKROLL = wallet
        for win, ey, m in (('IS', 2025, p.entry_date.dt.year <= 2025), ('OOT', 2026, p.entry_date.dt.year == 2026)):
            with contextlib.redirect_stdout(io.StringIO()):
                pl = rmc.build_payload(p[m].copy(), ey, '')
            s = pl['summary']; pts = pd.DataFrame(pl['points'])
            for arm, key in (('qty1', 'qty1'), ('qty2', 'strategy')):
                rows.append(dict(thr=thr, wallet=int(wallet), win=win, arm=arm, final=s[f'{key}_final'], ret_pct=s[f'{key}_total_return'], cagr=s[f'{key}_cagr'],
                                 max_dd=s[f'{key}_max_dd'], sharpe=s[f'{key}_sharpe'], sh_dollar=s[f'{key}_sharpe_dollar'], min_equity=round(pts[key].min(), 0),
                                 spy_final=s['spy_final'], spy_dd=s['spy_max_dd']))
        print(thr, wallet, 'done', flush=True)
R = pd.DataFrame(rows); pd.set_option('display.width', 250); print(R.to_string(index=False)); R.to_csv(f'{S}/wallet10k.csv', index=False); print('ALLDONE')
