"""Drift-free P_real: sweep the bull GROUND threshold (bear sleeve untouched) through the canon generator."""
import sys, io, contextlib, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); sys.path.insert(0, 'research'); sys.path.insert(0, 'research/p_real_demean_2026_09_28')
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
from sma_bull_regime_sweep import prior_spy_bull
from demean_lib import p_real_demeaned, trailing_ret, _canon_p_real

def frame(variant):
    ec.p_real = _canon_p_real if variant == 'canon' else p_real_demeaned
    with contextlib.redirect_stdout(io.StringIO()):
        c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
    c['bull_ok'] = prior_spy_bull(c.entry_date, spy, 100)
    return c

def picks(c, thr):
    bull = c[c.spread_type.eq('bull_put') & ~c.exdiv_hit & ~c.earnings_hit & c.bull_ok & (c.parity_pct > ec.PARITY_MIN_PCT) & (c.GROUND >= thr)]
    bear = c[c.spread_type.eq('bear_call') & ~c.exdiv_hit & ~c.earnings_hit & c[rbr.REGIME] & (c.bear_parity_pct > rbr.PARITY) & (c.GROUND >= rbr.GROUND)]
    return rbr.enrich(pd.concat([realize(bull, ec.TOP_N), realize(bear, rbr.CAP)], ignore_index=True))

CLS = ec.backtest_closes()
rows = []
for variant, thrs in (('canon', [0.005]), ('demeaned', [0.005, 0.003, 0.002, 0.001, 0.0005, 0.0, -1.0])):
    c = frame(variant)
    for thr in thrs:
        p = picks(c, thr)
        for win, end_year, m in (('IS', 2025, p.entry_date.dt.year <= 2025), ('OOT', 2026, p.entry_date.dt.year == 2026)):
            q = p[m].copy()
            with contextlib.redirect_stdout(io.StringIO()):
                s = rmc.build_payload(q, end_year, '')['summary']
            rows.append(dict(variant=variant, thr=thr, win=win, n=s['n_trades'], final=s['strategy_final'], sh_dollar=s['strategy_sharpe_dollar'],
                             max_dd=s['strategy_max_dd'], yield_pct=s['strategy_yield'], med_1y=round(np.nanmedian(trailing_ret(q, CLS)) * 100, 1)))
        print(rows[-2], rows[-1], flush=True)
T = pd.DataFrame(rows); pd.set_option('display.width', 250)
print(T.pivot_table(index=['variant', 'thr'], columns='win', values=['n', 'final', 'sh_dollar', 'max_dd', 'yield_pct', 'med_1y'], sort=False).to_string())
T.to_csv('research/p_real_demean_2026_09_28/thr_sweep.csv', index=False)
