# ATM IV per (Symbol, DataDate, ExpirationDate) for DTE<=45: median IV of the 6 strikes nearest the money with bid>0.
import numpy as np, pandas as pd, time, sys
T0 = time.time(); out = []
paths = [f'output/{y}_sp500_last.parquet' for y in range(2020, 2026)] + ['output/2026_sp500_last_oot_combined.parquet']
import sys; sys.path.insert(0, "."); import config; SP = set(config.SP100_TICKERS)
for p in paths:
    d = pd.read_parquet(p, columns=['Symbol', 'DataDate', 'ExpirationDate', 'DTE', 'StrikePrice', 'UnderlyingPrice', 'ImpliedVolatility', 'BidPrice'])
    d = d[d.Symbol.isin(SP) & d.DTE.between(1, 45) & (d.BidPrice > 0) & (d.ImpliedVolatility > 0) & (d.UnderlyingPrice > 0)]
    d['am'] = (np.log(d.StrikePrice / d.UnderlyingPrice)).abs()
    d = d.sort_values(['Symbol', 'DataDate', 'ExpirationDate', 'am'])
    d['rk'] = d.groupby(['Symbol', 'DataDate', 'ExpirationDate']).cumcount()
    g = d[d.rk < 6].groupby(['Symbol', 'DataDate', 'ExpirationDate']).agg(atm_iv=('ImpliedVolatility', 'median'), DTE=('DTE', 'first'), n=('rk', 'size')).reset_index()
    out.append(g); print(p, len(g), f'[{time.time()-T0:.0f}s]', flush=True)
T = pd.concat(out, ignore_index=True); T.to_parquet('research/dkl_2026_09_13/term_atm.parquet'); print('wrote', len(T))
