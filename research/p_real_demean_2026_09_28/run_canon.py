"""Drift-free P_real vs canon, through the CANON generator research/report_bear_regime.py
(bull top-10 + bear sleeve, earnings/ex-div gates, 2020-08 start). Nothing written to live/data.
    python3 research/p_real_demean_2026_09_28/run_canon.py
"""
import sys, io, contextlib, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); sys.path.insert(0, 'research')
import ent_canon as ec
import report_mid_canon as rmc
import report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
from sma_bull_regime_sweep import prior_spy_bull
sys.path.insert(0, 'research/p_real_demean_2026_09_28')
from run import p_real_demeaned, trailing_ret, _canon_p_real

def picks_for(variant):
    ec.p_real = _canon_p_real if variant == 'canon' else p_real_demeaned
    with contextlib.redirect_stdout(io.StringIO()):
        c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
    bull = c[c.spread_type.eq('bull_put') & ~c.exdiv_hit & ~c.earnings_hit & prior_spy_bull(c.entry_date, spy, 100)
             & (c.parity_pct > ec.PARITY_MIN_PCT) & (c.GROUND >= ec.THR)]
    bear = c[c.spread_type.eq('bear_call') & ~c.exdiv_hit & ~c.earnings_hit & c[rbr.REGIME]
             & (c.bear_parity_pct > rbr.PARITY) & (c.GROUND >= rbr.GROUND)]
    return rbr.enrich(pd.concat([realize(bull, ec.TOP_N), realize(bear, rbr.CAP)], ignore_index=True))

def key(df):
    return set(zip(df.ticker, pd.to_datetime(df.entry_date).dt.strftime('%Y-%m-%d'), df.short_strike.round(2), df.long_strike.round(2)))

CLS = ec.backtest_closes()
P = {v: picks_for(v) for v in ('canon', 'demeaned')}
rows = []
for win, end_year, sel in (('IS', 2025, lambda p: p[p.entry_date.dt.year <= 2025]), ('OOT', 2026, lambda p: p[p.entry_date.dt.year == 2026])):
    for v in ('canon', 'demeaned'):
        p = sel(P[v]).copy()
        with contextlib.redirect_stdout(io.StringIO()):
            s = rmc.build_payload(p, end_year, f'{win} {v}')['summary']
        tr = trailing_ret(p, CLS)
        rows.append(dict(win=win, variant=v, n=s['n_trades'], n_bear=int((p.spread_type == 'bear_call').sum()),
                         qty2_final=s['strategy_final'], qty2_ret=s['strategy_total_return'], sh_dollar=s['strategy_sharpe_dollar'],
                         sh_wk=s['strategy_sharpe_weekly'], max_dd=s['strategy_max_dd'], yield_pct=s.get('strategy_yield'),
                         med_1y_ret=round(np.nanmedian(tr) * 100, 1),
                         overlap_pct=round(len(key(p) & key(sel(P['canon']))) / max(len(key(p)), 1) * 100, 1)))
        yr = p.groupby(p.entry_date.dt.year).pnl_per_contract.agg(['sum', 'count'])
        print(f'{win} {v} per-year $/contract:', {int(y): (round(r['sum']), int(r['count'])) for y, r in yr.iterrows()}, flush=True)
for v in P: P[v].to_parquet(f"research/p_real_demean_2026_09_28/picks_{v}.parquet")
T = pd.DataFrame(rows); pd.set_option('display.width', 250)
print(T.to_string(index=False)); T.to_csv('research/p_real_demean_2026_09_28/results_canon.csv', index=False)
