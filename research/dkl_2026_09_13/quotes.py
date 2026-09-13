import sys, os, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dkl_eval as de
from dkl_eval import *
SCR = de.SCR; pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
IS = pd.read_parquet(f'{SCR}/feat3_IS.parquet').dropna(subset=['EV']); OO = pd.read_parquet(f'{SCR}/feat3_OOT.parquet').dropna(subset=['EV'])
for C in (IS, OO):
    C['nat'] = (C.BidPrice - C.L_AskPrice).round(4)                     # natural: sell short at bid, buy long at ask
    C['qw'] = ((C.AskPrice - C.BidPrice) + (C.L_AskPrice - C.L_BidPrice)) / C.net_credit   # combined book width / mid credit
def book_nat(sel, end):
    s = sel.copy(); s['credit'] = s.nat; s['ml'] = (s.width - s.credit).round(4); s = s[(s.credit > 0) & (s.ml > 0)].copy()
    s['pnl'] = pnl_of(s, s.credit.values, s.ml.values)
    cal = pd.bdate_range(s.entry_date.min(), end); eq = 10000.0 + s.groupby('expiry_date')['pnl'].sum().reindex(cal, fill_value=0).cumsum()
    w = eq.resample('W-FRI').last().ffill().pct_change().dropna(); sd = w.std(ddof=0)
    return dict(n=len(s), final=float(eq.iloc[-1]), sharpe=float(w.mean()*np.sqrt(52)/sd) if sd > 0 else 0.0,
                dd=100*float(((eq-eq.cummax())/eq.cummax()).min()), dropped=len(sel)-len(s))
print('=== quote quality of the selected books (mid basis) ===')
for dc, ks in (('D_emp_iv', (0, 8, 16, 24)), ('D_cert_iv', (2, 3, 4))):
    for k in ks:
        s = select(IS, 'EV', k, dc)
        print(f'{dc} k={k:>2}: n={len(s):>5} med credit ${s.net_credit.median():.2f}  med book-width/credit {s.qw.median():.2f}  '
              f'share qw>1 {(s.qw>1).mean():.1%}  share natural<=0 {(s.nat<=0).mean():.1%}  med bid-ask short ${(s.AskPrice-s.BidPrice).median():.2f}')
for dc, ks in (('D_emp_iv', (0, 4, 8, 12, 16, 24)), ('D_cert_iv', (0, 2, 3, 4))):
    print(f'\n=== {dc}: P&L at NATURAL credit (scoring unchanged, mid) ===')
    rows = []
    for k in ks:
        a = book_nat(select(IS, 'EV', k, dc), pd.Timestamp('2025-12-31')); b = book_nat(select(OO, 'EV', k, dc), pd.Timestamp('2026-08-31'))
        rows.append(dict(k=k, is_n=a['n'], is_fin=a['final'], is_sh=a['sharpe'], is_dd=a['dd'], oot_n=b['n'], oot_fin=b['final'], oot_sh=b['sharpe'], oot_dd=b['dd']))
    print(pd.DataFrame(rows).to_string(index=False, float_format=fmt))
    print(f'=== {dc}: quote-width filter (book width <= credit), 0.80x mid ===')
    ISf, OOf = IS[IS.qw <= 1.0], OO[OO.qw <= 1.0]
    print(f'   candidates kept IS {len(ISf)/len(IS):.1%}  OOT {len(OOf)/len(OO):.1%}')
    print(sweep(ISf, OOf, dc, ks=ks).to_string(index=False, float_format=fmt))
