"""Gate 2 for the MOMENTUM overlay: does it add lift ON TOP OF GROUND?

Uses the CANONICAL scored+realized cache (output/sweep_midmkt_v2_2020_25.parquet,
k=10, thr=0.05, top-5/day, 0.80x mid fill, partial-WIN haircut baked into pn_80)
exactly as report_mid_canon.select_picks does. Then joins 20-day trailing
momentum (from the gate-1 candidate cache) and asks two questions on the
QUALIFYING picks only:

  A. Directional gradient: within qualifying bull_puts / bear_calls, does the
     mom_20 P&L / breach gradient survive? (the honest "does it add info" test)
  B. Practical overlay: if we DROP picks fighting momentum (bear_call at high
     momentum, bull_put at low momentum), does P&L/contract and total P&L improve
     vs. taking every qualifying pick?
"""
import os, sys
import numpy as np, pandas as pd

K_VAL, THR, FILL = 10.0, 0.05, 0.80
SPY_CSV = 'data/spy_us_d.csv'
CANON = 'output/sweep_midmkt_v2_2020_25.parquet'
GATE1 = 'output/momentum_gate_candidates.parquet'


def select_picks():
    R = pd.read_parquet(CANON)
    R['entry_date'] = pd.to_datetime(R['entry_date'])
    spy = pd.read_csv(SPY_CSV, parse_dates=['Date'])
    tdays = set(spy['Date'].dt.normalize())
    R = R[R['entry_date'].dt.normalize().isin(tdays)].copy()
    R['GROUND'] = (np.exp(R['G']) - 1.0) * np.exp(-K_VAL * R['DKL'])
    sel = (R[R['GROUND'] >= THR]
           .sort_values(['entry_date', 'GROUND'], ascending=[True, False])
           .groupby('entry_date').head(5)).copy()
    sel['pnl'] = sel['pnl_80']
    sel['breach'] = (sel['_outcome'] != 'WIN')
    return sel


def mom_lookup():
    g = pd.read_parquet(GATE1, columns=['ticker', 'entry_date', 'mom_20'])
    g['entry_date'] = pd.to_datetime(g['entry_date'])
    return g.drop_duplicates(['ticker', 'entry_date'])


def gradient(sel):
    print(f'\n{"="*66}\nA. mom_20 gradient among QUALIFYING picks (canon selection)\n{"="*66}')
    for st in ['bull_put', 'bear_call']:
        sub = sel[(sel['spread_type'] == st)].dropna(subset=['mom_20']).copy()
        print(f'\n  {st}  (n={len(sub):,})')
        if len(sub) < 100:
            print('    too small'); continue
        try:
            sub['q'] = pd.qcut(sub['mom_20'], 5, labels=[1,2,3,4,5], duplicates='drop')
        except ValueError:
            print('    cannot quintile'); continue
        g = sub.groupby('q', observed=True)
        pnl, br, n = g['pnl'].mean(), g['breach'].mean()*100, g.size()
        print(f'    {"Q":>2} {"n":>5} {"P&L/ctr":>9} {"breach%":>8}')
        for q in sorted(sub['q'].dropna().unique()):
            print(f'    {int(q):>2} {int(n[q]):>5} {pnl[q]:>9.2f} {br[q]:>8.1f}')
        if 5 in pnl.index and 1 in pnl.index:
            a = sub[sub['q']==5]['pnl']; b = sub[sub['q']==1]['pnl']
            se = np.sqrt(a.var(ddof=1)/len(a)+b.var(ddof=1)/len(b))
            t = (a.mean()-b.mean())/se if se>0 else float('nan')
            print(f'    Q5-Q1 P&L: {a.mean()-b.mean():+.2f}  t={t:+.2f}')


def overlay(sel):
    print(f'\n{"="*66}\nB. Practical overlay: drop picks fighting momentum\n{"="*66}')
    d = sel.dropna(subset=['mom_20']).copy()
    # per-day median momentum split is per-pick sign of mom_20; use 0 as the
    # trend divide (positive 20d return = uptrend). "Fighting" = bear_call in an
    # uptrend or bull_put in a downtrend.
    fight = (((d['spread_type']=='bear_call') & (d['mom_20'] > 0)) |
             ((d['spread_type']=='bull_put')  & (d['mom_20'] < 0)))
    kept = d[~fight]; dropped = d[fight]
    def line(name, x):
        if len(x)==0: print(f'  {name:<22} n=0'); return
        print(f'  {name:<22} n={len(x):>5}  P&L/ctr={x["pnl"].mean():>7.2f}  '
              f'total_qty2=${(x["pnl"]*2).sum():>10,.0f}  breach={x["breach"].mean()*100:>4.1f}%')
    line('ALL qualifying', d)
    line('KEPT (with trend)', kept)
    line('DROPPED (fighting)', dropped)
    # also a threshold variant: only drop when the trend is meaningful (|mom|>3%)
    strong = fight & (d['mom_20'].abs() > 0.03)
    kept2 = d[~strong]
    line('KEPT (|mom|>3% only)', kept2)


if __name__ == '__main__':
    if not os.path.exists(GATE1):
        sys.exit(f'missing {GATE1} — run backtest_momentum_signal.py first')
    sel = select_picks()
    m = mom_lookup()
    sel = sel.merge(m, on=['ticker', 'entry_date'], how='left')
    cov = sel['mom_20'].notna().mean()*100
    print(f'Qualifying canon picks: {len(sel):,}  (mom_20 coverage {cov:.0f}%)')
    print(f'  bull_put={int((sel.spread_type=="bull_put").sum()):,}  '
          f'bear_call={int((sel.spread_type=="bear_call").sum()):,}')
    print(f'  entry range {sel.entry_date.min().date()} -> {sel.entry_date.max().date()}')
    gradient(sel)
    overlay(sel)
