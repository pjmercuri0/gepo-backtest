"""Market gap on stocks only (user 2026-09-16): SP100_TICKERS minus SPY/QQQ/IWM (ETFs double-count the
stocks) minus SPXW/RUTW (index roots) minus MMC (dead symbol) = 93 names. Walk-forward refit per year and
compare canon / current 83-name gap / 93-stock gap. Writes only output/market_gap_backtest_93stocks.parquet.

    python3 research/ta_direction/ta_gap93.py
"""
import sys, io, os, contextlib, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config, ent_canon as ec, report_ent_canon as rec, report_mid_canon as rmc
import build_market_gap as bmg, fit_market_gap as fmg

U93 = tuple(sorted(t for t in config.SP100_TICKERS if t not in ('SPY', 'QQQ', 'IWM', 'SPXW', 'RUTW', 'MMC')))
OUT93 = 'output/market_gap_backtest_93stocks.parquet'
U83, FIT83, SER83 = ec.GAP_UNIVERSE, dict(ec.GAP_FIT), rec.GAP_SERIES
print(f'93-stock universe: {len(U93)} names; in 83 but not 93: {sorted(set(U83) - set(U93))}; new: {sorted(set(U93) - set(U83))}', flush=True)

if not os.path.exists(OUT93):
    bars = []
    for i, t in enumerate(U93, 1):
        y = bmg.local(t)
        try:
            z = bmg.api(t); y = pd.concat([y, z[~z.date.isin(set(y.date))]], ignore_index=True)
        except Exception as e:
            print(f'  {t}: API failed {str(e)[:50]}', flush=True)
        y['ticker'] = t; bars.append(y); time.sleep(0.25)
        if i % 31 == 0 or i == len(U93): print(f'bars {i}/{len(U93)} ({100*i/len(U93):.0f}%)', flush=True)
    ec.GAP_UNIVERSE = U93
    ec.market_gap(pd.concat(bars, ignore_index=True)).to_parquet(OUT93, index=False)
    ec.GAP_UNIVERSE = U83
g83 = pd.read_parquet(SER83).set_index('date'); g93 = pd.read_parquet(OUT93).set_index('date')
j = pd.concat([g83.mkt_gap.rename('g83'), g93.mkt_gap.rename('g93'), g93.n_names.rename('n93')], axis=1).dropna()
print('corr(83-name gap, 93-stock gap) by year:', {y: round(g.g83.corr(g.g93), 3) for y, g in j.groupby(j.index.year)}, '| names per day 93-set median', int(j.n93.median()), flush=True)

# walk-forward fits on the 93-stock series
rec.GAP_SERIES = OUT93
T = fmg.training_frame(); FIT93 = {}
for Y in (2021, 2022, 2023, 2024, 2025, 2026):
    m, s, b, n = fmg.fit(T, Y); FIT93[Y] = (m, s, b)
    print(f'    {Y}: ({m!r}, {s!r}, {b!r}),   # {n:,} stock-days   [83-name beta {FIT83[Y][2]:.4f}]', flush=True)
T['yr'] = T.entry_date.dt.year
print('day-level corr(market gap, mean fwd move) by year, 93-stock:',
      {y: round(g.groupby('entry_date')[['x', 'y']].mean().corr().iloc[0, 1], 3) for y, g in T.groupby('yr')}, flush=True)

def metr(sel, ey):
    with contextlib.redirect_stdout(io.StringIO()):
        s = rmc.build_payload(sel.sort_values('entry_date_dt').reset_index(drop=True), ey, '')['summary']
    pw = ((sel._outcome == 'WIN') | ((sel._outcome == 'PARTIAL') & (sel.pnl_per_contract > 0))).mean()
    return dict(n=len(sel), pnl=round(sel.pnl_per_contract.sum()), win=round(100 * (sel._outcome == 'WIN').mean(), 1),
                winp=round(100 * pw, 1), sh=s['qty1_sharpe_weekly'], dd=s['qty1_max_dd'])
CONF = {'canon': (0.0, FIT83, SER83), 'gap83': (1.0, FIT83, SER83), 'gap93': (1.0, FIT93, OUT93)}
rows = []
for name, (gam, fit, ser) in CONF.items():
    ec.GAP_GAMMA, ec.GAP_FIT, rec.GAP_SERIES = gam, fit, ser
    for win, ey in (('IS', 2025), ('OOT', 2026)):
        S = rec.select(win); yr = S.entry_date_dt.dt.year
        periods = [(str(y), yr == y, y) for y in sorted(yr.unique())]
        if win == 'IS': periods += [('2021-25', yr >= 2021, 2025), ('IS 2020-25', yr > 0, 2025)]
        for lab, m, e in periods: rows.append(dict(config=name, period=lab, **metr(S[m], e)))
    print(f'evaluated {name}', flush=True)
X = pd.DataFrame(rows); order = ['2020', '2021', '2022', '2023', '2024', '2025', '2021-25', 'IS 2020-25', '2026']
pd.set_option('display.width', 250)
for col in ('pnl', 'sh', 'dd', 'win', 'winp', 'n'):
    P = X.pivot(index='period', columns='config', values=col).reindex(order)[['canon', 'gap83', 'gap93']]
    print(f'\n--- {col} ---'); print(P.to_string())
X.to_csv('research/ta_direction/ta_gap93_results.csv', index=False)
