"""Handoff §0.65 second task: select/rank bull puts on D_ent alone (drift-free canon, same gates and caps).
    python3 research/model_beats_market_2026_09_29/run_dent.py
"""
import sys, io, contextlib, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); sys.path.insert(0, 'research'); sys.path.insert(0, 'research/p_real_demean_2026_09_28')
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
from sma_bull_regime_sweep import prior_spy_bull
from demean_lib import trailing_ret
assert ec.P_REAL_DEMEAN and ec.THR == 0.0005

with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
c['bull_ok'] = prior_spy_bull(c.entry_date, spy, 100)
BASE = c.spread_type.eq('bull_put') & ~c.exdiv_hit & ~c.earnings_hit & c.bull_ok & (c.parity_pct > ec.PARITY_MIN_PCT)
BEAR = c[c.spread_type.eq('bear_call') & ~c.exdiv_hit & ~c.earnings_hit & c[rbr.REGIME] & (c.bear_parity_pct > rbr.PARITY) & (c.GROUND >= rbr.GROUND)]

def picks(bull_mask, rank_by):
    bull = c[BASE & bull_mask].copy()
    if rank_by == 'dent':
        bull['GROUND_true'] = bull.GROUND; bull['GROUND'] = -bull.D_ent      # realize() sorts GROUND desc -> lowest D_ent first
    sel = realize(bull, ec.TOP_N)
    if rank_by == 'dent':
        sel['GROUND'] = sel.GROUND_true
    return rbr.enrich(pd.concat([sel, realize(BEAR, rbr.CAP)], ignore_index=True))

CLS = ec.backtest_closes()
T = np.ones(len(c), bool)
variants = [('§0.64 baseline: GROUND>=0.0005, rank GROUND', (c.GROUND >= ec.THR).values, 'ground'),
            ('no score filter, rank GROUND (thr=-1)', T, 'ground'),
            ('rank lowest D_ent, no threshold', T, 'dent'),
            ('GROUND>=0.0005 then rank lowest D_ent', (c.GROUND >= ec.THR).values, 'dent'),
            ('D_ent <= 0.050 (p20), rank GROUND', (c.D_ent <= 0.050).values, 'ground'),
            ('D_ent <= 0.086 (p40), rank GROUND', (c.D_ent <= 0.086).values, 'ground'),
            ('D_ent <= 0.128 (p60), rank GROUND', (c.D_ent <= 0.128).values, 'ground'),
            ('D_ent <= 0.086 (p40), rank lowest D_ent', (c.D_ent <= 0.086).values, 'dent')]
rows = []
for name, mask, rank in variants:
    p = picks(mask, rank)
    for win, end_year, m in (('IS', 2025, p.entry_date.dt.year <= 2025), ('OOT', 2026, p.entry_date.dt.year == 2026)):
        q = p[m].copy()
        with contextlib.redirect_stdout(io.StringIO()):
            s = rmc.build_payload(q, end_year, '')['summary']
        rows.append(dict(variant=name, win=win, n=s['n_trades'], final=s['strategy_final'], sh_dollar=s['strategy_sharpe_dollar'],
                         max_dd=s['strategy_max_dd'], yield_pct=s['strategy_yield'], med_1y=round(np.nanmedian(trailing_ret(q, CLS)) * 100, 1),
                         med_dent=round(float(q[q.spread_type == 'bull_put'].D_ent.median()), 3)))
    print(name, 'done', flush=True)
R = pd.DataFrame(rows); pd.set_option('display.width', 250)
print(R.pivot_table(index='variant', columns='win', values=['n', 'final', 'sh_dollar', 'max_dd', 'yield_pct', 'med_1y', 'med_dent'], sort=False).to_string())
R.to_csv('research/model_beats_market_2026_09_29/results_dent.csv', index=False)
