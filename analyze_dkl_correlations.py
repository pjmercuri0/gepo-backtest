"""Correlation analysis: GROUND scores vs realized P&L for all candidates.

Loads the cached realized DataFrame from test_dkl_sweep.py and computes:
  - Pearson + Spearman correlations between each GROUND flavor and pnl_per_ctr
  - Decile analysis: split candidates into 10 GROUND-score buckets, show mean P&L
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, '.')

CACHE = 'output/sweep_realized_2023_25.parquet'
R = pd.read_parquet(CACHE)
print(f'Loaded {len(R):,} candidates from {CACHE}\n')

# Restrict to candidates with finite GROUND for all three (apples-to-apples)
mask = R[['G_uni','G_fwd','G_bwd','pnl_per_ctr']].notna().all(axis=1)
R = R[mask].copy()
print(f'After dropping NaN GROUND/PnL: {len(R):,} candidates\n')

flavors = [('G_uni', 'UNIFORM'),
           ('G_fwd', 'FORWARD  D(hist‖delta)'),
           ('G_bwd', 'BACKWARD D(delta‖hist)')]

# ──────────────────────────────────────────────────────────────────────
# Correlations
# ──────────────────────────────────────────────────────────────────────
print('══ Correlation: GROUND vs realized pnl_per_ctr ══')
print(f'{"flavor":<24} {"Pearson":>9} {"Spearman":>10}')
print('-'*46)
for col, label in flavors:
    pear = R[col].corr(R['pnl_per_ctr'], method='pearson')
    spear = R[col].corr(R['pnl_per_ctr'], method='spearman')
    print(f'{label:<24} {pear:>+9.4f} {spear:>+10.4f}')

# ──────────────────────────────────────────────────────────────────────
# Per day-of-week
# ──────────────────────────────────────────────────────────────────────
R['entry_dow'] = pd.to_datetime(R['entry_date']).dt.dayofweek
dow_names = {0:'Mon', 1:'Tue', 2:'Wed', 3:'Thu'}
print('\n══ Per-day-of-week Spearman correlation ══')
print(f'{"flavor":<24}', end='')
for dow in [0,1,2,3]: print(f' {dow_names[dow]:>8}', end='')
print()
print('-'*60)
for col, label in flavors:
    print(f'{label:<24}', end='')
    for dow in [0,1,2,3]:
        sub = R[R['entry_dow']==dow]
        if len(sub) > 10:
            sp = sub[col].corr(sub['pnl_per_ctr'], method='spearman')
            print(f' {sp:>+8.4f}', end='')
        else:
            print(f' {"--":>8}', end='')
    print()

# ──────────────────────────────────────────────────────────────────────
# Decile analysis: 10 GROUND-score bins, show mean / win-rate / n per bin
# ──────────────────────────────────────────────────────────────────────
print('\n══ Decile analysis (mean pnl_per_ctr by GROUND-score decile) ══')
for col, label in flavors:
    R['_decile'] = pd.qcut(R[col], 10, labels=False, duplicates='drop')
    print(f'\n{label}:')
    print(f'  {"dec":>4} {"n":>6} {"mean_GROUND":>13} {"mean_pnl":>9} {"win%":>6}')
    print('  ' + '-'*45)
    for dec in sorted(R['_decile'].dropna().unique()):
        sub = R[R['_decile']==dec]
        print(f'  {int(dec):>4} {len(sub):>6} {sub[col].mean():>+13.4f} ${sub["pnl_per_ctr"].mean():>+7.2f}  {100*(sub["pnl_per_ctr"]>0).mean():>5.1f}%')

# ──────────────────────────────────────────────────────────────────────
# Top-decile vs bottom-decile P&L per flavor
# ──────────────────────────────────────────────────────────────────────
print('\n══ Top-10% vs bottom-10% P&L gap ══')
print(f'{"flavor":<24} {"top10%_mean":>11} {"bot10%_mean":>12} {"gap":>9}')
print('-'*60)
for col, label in flavors:
    R['_dec'] = pd.qcut(R[col], 10, labels=False, duplicates='drop')
    top = R[R['_dec']==9]['pnl_per_ctr'].mean()
    bot = R[R['_dec']==0]['pnl_per_ctr'].mean()
    print(f'{label:<24} ${top:>+9.2f} ${bot:>+10.2f} ${top-bot:>+7.2f}')
