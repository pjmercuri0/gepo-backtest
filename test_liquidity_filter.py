"""Sweep liquidity filter thresholds on cached picks.
filter = (short_BA + long_BA) / spread_width

Reject picks where this ratio > threshold.
For each threshold, report: picks kept, total P&L (canonical), Sharpe.
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, '.')

p = pd.read_parquet('output/picks_cache_k30.parquet')
p['short_ba']  = p['short_ask'] - p['short_bid']
p['long_ba']   = p['long_ask']  - p['long_bid']
p['ba_total']  = p['short_ba'] + p['long_ba']
p['ba_ratio']  = p['ba_total'] / p['spread_width']

# Apply ¹⁄₁₆ Kelly cap=5 sizing
def qty(r):
    ws = r.get('w_star'); ml_d = r['max_loss_dollar']
    if ws is None or pd.isna(ws) or ws <= 0 or ml_d <= 0: return 1
    return max(1, min(5, int(0.0625 * float(ws) * 10000 / ml_d)))
p['qty'] = p.apply(qty, axis=1)
p['pnl'] = p['qty'] * p['pnl_per_contract']
p['date'] = pd.to_datetime(p['entry_date'])

print(f'Total cached picks: {len(p)}')
print(f'\nbid-ask ratio distribution:')
for lo, hi in [(0,0.1),(0.1,0.2),(0.2,0.3),(0.3,0.4),(0.4,0.5),(0.5,0.7),(0.7,1.0),(1.0,99)]:
    n = ((p['ba_ratio']>=lo)&(p['ba_ratio']<hi)).sum()
    pct = 100*n/len(p)
    label = f'{lo:.1f}-{hi:.1f}' if hi<99 else f'{lo:.1f}+'
    bar = '█'*int(pct*0.5)
    print(f'  {label:>7}: {n:>4} ({pct:>5.1f}%) {bar}')

def sharpe(daily):
    sg = daily.std(ddof=0)
    return daily.mean()*np.sqrt(252)/sg if sg>0 else 0


print(f'\n══ Filter sweep (canonical P&L, ¹⁄₁₆K cap=5) ══')
print(f'{"threshold":>10} {"kept":>5} {"%kept":>6} {"profit":>9} {"$/tr":>7} {"win %":>6} {"Sharpe":>7}')
print('-'*60)
# Baseline: no filter
n = len(p); tot = p['pnl'].sum(); mu = p['pnl'].mean()
win = 100*(p['pnl']>0).mean()
sh = sharpe(p.groupby('date')['pnl'].sum())
print(f'{"NONE":>10} {n:>5} {100*n/len(p):>5.1f}% ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')

for thr in [1.00, 0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50]:
    keep = p[p['ba_ratio'] <= thr]
    n = len(keep); tot = keep['pnl'].sum(); mu = keep['pnl'].mean() if n else 0
    win = 100*(keep['pnl']>0).mean() if n else 0
    sh = sharpe(keep.groupby('date')['pnl'].sum())
    print(f'{thr:>10.2f} {n:>5} {100*n/len(p):>5.1f}% ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')


print(f'\n══ Dropped-picks analysis (picks with ba_ratio > 0.50) ══')
dropped = p[p['ba_ratio'] > 0.50]
print(f'  {len(dropped)} picks dropped')
print(f'  Their P&L: ${dropped["pnl"].sum():+,.0f}')
print(f'  Win rate: {100*(dropped["pnl"]>0).mean():.1f}%')
print(f'  Per-outcome:')
def outcome(r):
    spot = r['expiry_close']; ss = r['short_strike']; ls = r['long_strike']
    if r['spread_type']=='bull_put':
        return 'WIN' if spot >= ss else 'MAX_LOSS' if spot <= ls else 'BETWEEN'
    else:
        return 'WIN' if spot <= ss else 'MAX_LOSS' if spot >= ls else 'BETWEEN'
dropped = dropped.copy()
dropped['outcome'] = dropped.apply(outcome, axis=1)
for o in ['WIN','BETWEEN','MAX_LOSS']:
    sub = dropped[dropped['outcome']==o]
    if not sub.empty:
        print(f'    {o:<9} n={len(sub)}  P&L ${sub["pnl"].sum():+,.0f}')
