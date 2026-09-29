"""Gate ablation on the §0.64 drift-free canon: which gate carries the yield? Bull sleeve only; bear sleeve canon.
    python3 research/model_beats_market_2026_09_29/run_ablate.py
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
BEAR = c[c.spread_type.eq('bear_call') & ~c.exdiv_hit & ~c.earnings_hit & c[rbr.REGIME] & (c.bear_parity_pct > rbr.PARITY) & (c.GROUND >= rbr.GROUND)]
G = {'regime': c.bull_ok.values, 'parity': (c.parity_pct > ec.PARITY_MIN_PCT).values, 'earnings': (~c.earnings_hit).values,
     'exdiv': (~c.exdiv_hit).values, 'ground': (c.GROUND >= ec.THR).values}
BULL = c.spread_type.eq('bull_put').values

def picks(drop, bear=True):
    m = BULL.copy()
    for k, v in G.items():
        if k not in drop: m &= v
    parts = [realize(c[m], ec.TOP_N)] + ([realize(BEAR, rbr.CAP)] if bear else [])
    return rbr.enrich(pd.concat(parts, ignore_index=True))

CLS = ec.backtest_closes()
variants = [('§0.64 canon', (), True), ('bull sleeve only (no bear)', (), False),
            ('- regime', ('regime',), True), ('- parity', ('parity',), True), ('- earnings', ('earnings',), True),
            ('- exdiv', ('exdiv',), True), ('- ground thr', ('ground',), True),
            ('- all gates (top-10 bull puts by GROUND, any day)', tuple(G), True),
            ('- all gates, bull only', tuple(G), False)]
rows = []
for name, drop, bear in variants:
    p = picks(drop, bear)
    for win, end_year, m in (('IS', 2025, p.entry_date.dt.year <= 2025), ('OOT', 2026, p.entry_date.dt.year == 2026)):
        q = p[m].copy()
        with contextlib.redirect_stdout(io.StringIO()):
            s = rmc.build_payload(q, end_year, '')['summary']
        yr = q.groupby(q.entry_date.dt.year).pnl_per_contract.sum().round(0).astype(int).to_dict() if win == 'IS' else {}
        rows.append(dict(variant=name, win=win, n=s['n_trades'], final=s['strategy_final'], sh_dollar=s['strategy_sharpe_dollar'],
                         max_dd=s['strategy_max_dd'], yield_pct=s['strategy_yield'], win_rate=round((q._outcome == 'WIN').mean() * 100, 1),
                         med_1y=round(np.nanmedian(trailing_ret(q, CLS)) * 100, 1), years_neg=sum(v < 0 for v in yr.values())))
    print(name, 'done', flush=True)
R = pd.DataFrame(rows); pd.set_option('display.width', 250)
print(R.pivot_table(index='variant', columns='win', values=['n', 'final', 'sh_dollar', 'max_dd', 'yield_pct', 'win_rate', 'years_neg'], sort=False).to_string())
R.to_csv('research/model_beats_market_2026_09_29/results_ablate.csv', index=False)
