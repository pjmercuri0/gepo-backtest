"""Handoff §0.65: gate picks on G(model triple) > G(market triple) + eps at the same credit.
Runs the §0.64 drift-free canon through research/report_bear_regime.py with the gate added.
    python3 research/model_beats_market_2026_09_29/run.py
"""
import sys, io, contextlib, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.'); sys.path.insert(0, 'research'); sys.path.insert(0, 'research/p_real_demean_2026_09_28')
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
from sma_bull_regime_sweep import prior_spy_bull
from demean_lib import trailing_ret
assert ec.P_REAL_DEMEAN and ec.THR == 0.0005, (ec.P_REAL_DEMEAN, ec.THR)

with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
c['bull_ok'] = prior_spy_bull(c.entry_date, spy, 100)
b = c.model_credit.values / (c.width.values - c.model_credit.values)
_, c['G_mkt'] = ec.kelly(np.nan_to_num(c.q_win.values), np.nan_to_num(c.q_loss.values), np.nan_to_num(c.q_part.values), b)
_, c['G_mod'] = ec.kelly(np.nan_to_num(c.p.values), np.nan_to_num(c.q.values), np.nan_to_num(c.ro.values), b)
c['margin'] = c.G_mod - c.G_mkt
a = np.where(b >= 1, 0.0, (b - 1) / (2 * b))
c['ev_lin_mkt'] = c.q_win * b + c.q_part * a * b - c.q_loss      # linear expected payoff (units of max loss) under the market triple
print('market-triple growth across all candidates: G_mkt mean %.4f, median %.4f, >0 share %.1f%%; linear EV under Q mean %.4f, median %.4f'
      % (c.G_mkt.mean(), c.G_mkt.median(), (c.G_mkt > 0).mean() * 100, c.ev_lin_mkt.mean(), c.ev_lin_mkt.median()))
print('  b>=1 share %.1f%%; G_mkt>0 share when b>=1: %.1f%%, when b<1: %.1f%%' % ((b >= 1).mean() * 100, (c.G_mkt[b >= 1] > 0).mean() * 100, (c.G_mkt[b < 1] > 0).mean() * 100))

def picks(gate_bull, gate_bear):
    bull = c[c.spread_type.eq('bull_put') & ~c.exdiv_hit & ~c.earnings_hit & c.bull_ok & (c.parity_pct > ec.PARITY_MIN_PCT) & (c.GROUND >= ec.THR) & gate_bull]
    bear = c[c.spread_type.eq('bear_call') & ~c.exdiv_hit & ~c.earnings_hit & c[rbr.REGIME] & (c.bear_parity_pct > rbr.PARITY) & (c.GROUND >= rbr.GROUND) & gate_bear]
    return rbr.enrich(pd.concat([realize(bull, ec.TOP_N), realize(bear, rbr.CAP)], ignore_index=True))

CLS = ec.backtest_closes()
T = np.ones(len(c), bool)
variants = [('§0.64 baseline', T, T),
            ('bull: G_mod > G_mkt', (c.margin > 0).values, T),
            ('bull: margin > 0.001', (c.margin > 0.001).values, T),
            ('bull: margin > 0.002', (c.margin > 0.002).values, T),
            ('both sleeves: G_mod > G_mkt', (c.margin > 0).values, (c.margin > 0).values)]
rows = []
for name, gb, gr in variants:
    p = picks(gb, gr)
    for win, end_year, m in (('IS', 2025, p.entry_date.dt.year <= 2025), ('OOT', 2026, p.entry_date.dt.year == 2026)):
        q = p[m].copy()
        with contextlib.redirect_stdout(io.StringIO()):
            s = rmc.build_payload(q, end_year, '')['summary']
        rows.append(dict(variant=name, win=win, n=s['n_trades'], n_bear=int((q.spread_type == 'bear_call').sum()), final=s['strategy_final'],
                         sh_dollar=s['strategy_sharpe_dollar'], max_dd=s['strategy_max_dd'], yield_pct=s['strategy_yield'],
                         med_1y=round(np.nanmedian(trailing_ret(q, CLS)) * 100, 1)))
    print(name, 'done', flush=True)
R = pd.DataFrame(rows); pd.set_option('display.width', 250)
print(R.pivot_table(index='variant', columns='win', values=['n', 'n_bear', 'final', 'sh_dollar', 'max_dd', 'yield_pct', 'med_1y'], sort=False).to_string())
R.to_csv('research/model_beats_market_2026_09_29/results.csv', index=False)
