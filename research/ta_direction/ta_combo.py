"""Evaluate the rules the two searches found, alone and combined, on IS 2020-25 (per year) and OOT 2026."""
import sys, runpy, io, contextlib, warnings; warnings.filterwarnings('ignore')
sys.argv = ['ta_search.py', '--mode', 'is']
with contextlib.redirect_stdout(io.StringIO()):
    G = runpy.run_path('research/ta_direction/ta_search.py')
import pandas as pd, numpy as np
C, select, metr = G['C'], G['select'], G['metr']
pd.set_option('display.width', 250)
R = {
 'A close-location extreme (split search)': lambda: (C.clv_s >= 0.4279),
 'B bear_call, 5d/20d range expanding':      lambda: (C.spread_type == 'bear_call') & (C.atr_ratio >= 1.2695),
 'C bear_call, Bollinger width top 13%':     lambda: (C.spread_type == 'bear_call') & (C.bbw_pct >= 0.8667),
}
combos = {'canon': [], 'A': ['A'], 'B': ['B'], 'C': ['C'], 'B+C': ['B', 'C'], 'A+B+C': ['A', 'B', 'C']}
key = {k[0]: k for k in R}
rows = []
for name, parts in combos.items():
    vm = pd.Series(False, index=C.index)
    for p in parts: vm |= R[key[p]]().fillna(False)
    r = {'rules': name}
    IS = select(C[C.win == 'IS'], vm[C.win == 'IS']); m = metr(IS, 2025)
    r.update({'IS n': m['n'], 'IS P&L': m['pnl'], 'IS win%': m['win'], 'IS yield': m['yld'], 'IS Sh': m['sh'], 'IS DD': m['dd']})
    for y in range(2020, 2026):
        g = IS[IS.entry_date.dt.year == y]; r[str(y)] = round(g.pnl_per_contract.sum())
    O = select(C[C.win == 'OOT'], vm[C.win == 'OOT']); m = metr(O, 2026)
    r.update({'OOT n': m['n'], 'OOT P&L': m['pnl'], 'OOT yield': m['yld'], 'OOT Sh': m['sh'], 'OOT DD': m['dd']})
    rows.append(r); print('done', name, flush=True)
X = pd.DataFrame(rows)
base = X.iloc[0]
X['years beat canon'] = [sum(r[str(y)] > base[str(y)] for y in range(2020, 2026)) for _, r in X.iterrows()]
print(X.to_string(index=False)); X.to_csv('research/ta_direction/ta_combo.csv', index=False)
