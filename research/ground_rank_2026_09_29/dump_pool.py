import sys, io, contextlib, warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); sys.path.insert(0, 'research'); sys.path.insert(0, 'research/p_real_demean_2026_09_28')
import ent_canon as ec, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
print('K', ec.K, 'THR', ec.THR, 'TOP_N', ec.TOP_N, 'FILL', ec.FILL_MULT, 'PARITY_MIN', ec.PARITY_MIN_PCT, 'bear cap', rbr.CAP, 'bear GROUND', rbr.GROUND)
print('cols', list(c.columns))
pool = c[c.spread_type.eq('bull_put') & ~c.earnings_hit & ~c.exdiv_hit]
print('bull pool no thr', len(pool), ' >=thr', int((pool.GROUND>=ec.THR).sum()))
r = rbr.enrich(realize(pool, 10**6))
r.to_parquet(sys.argv[1]); print('saved', len(r), r.entry_date.min(), r.entry_date.max())
