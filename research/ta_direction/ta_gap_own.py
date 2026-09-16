"""Own-name gap only (user 2026-09-16: "I don't want it to rely on the market at all"). Each candidate's drift uses
ONLY its own stock's opening gap in ATR units: mu = beta_Y * clip((gap - mean_Y)/sd_Y, +-4) * sigma_d * sqrt(DTE),
(mean, sd, beta) fitted walk-forward on stock-days before entry year Y. No cross-name average anywhere.
Writes output/name_gaps_backtest.parquet (new). Compares canon / market gap (current canon) / own gap.

    python3 research/ta_direction/ta_gap_own.py
"""
import sys, io, os, contextlib, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import ent_canon as ec, report_ent_canon as rec, report_mid_canon as rmc
import build_market_gap as bmg, fit_market_gap as fmg

OUT = 'output/name_gaps_backtest.parquet'
if not os.path.exists(OUT):
    bars = []
    for i, t in enumerate(ec.GAP_UNIVERSE, 1):
        y = bmg.local(t)
        try:
            z = bmg.api(t); z = z[~z.date.isin(set(y.date))]
            if len(z): y = pd.concat([y, z], ignore_index=True)
        except Exception as e:
            print(f'  {t}: API failed {str(e)[:50]}', flush=True)
        y['ticker'] = t; bars.append(y); time.sleep(0.25)
        if i % 21 == 0 or i == len(ec.GAP_UNIVERSE): print(f'bars {i}/{len(ec.GAP_UNIVERSE)} ({100*i/len(ec.GAP_UNIVERSE):.0f}%)', flush=True)
    ec.name_gaps(pd.concat(bars, ignore_index=True)).to_parquet(OUT, index=False)
NG = pd.read_parquet(OUT); NG['date'] = pd.to_datetime(NG.date).dt.normalize()
OWN = NG.set_index(['ticker', 'date']).gap

# ── training frame with x = own gap, walk-forward fits ──
T = fmg.training_frame()
T['x'] = pd.MultiIndex.from_arrays([T.ticker, T.entry_date]).map(OWN).astype(float)
FITOWN = {}
for Y in (2021, 2022, 2023, 2024, 2025, 2026):
    m, s, b, n = fmg.fit(T, Y); FITOWN[Y] = (m, s, b)
    print(f'  own-gap fit {Y}: mean {m:+.4f} sd {s:.4f} beta {b:+.4f}  ({n:,} stock-days)', flush=True)

# ── does a stock's own gap predict its own move? pooled and within-day, per year ──
T['yr'] = T.entry_date.dt.year; ok = T.dropna(subset=['x', 'y'])
rows = []
for y_, g in ok.groupby('yr'):
    pooled = g.x.rank().corr(g.y.rank())
    wd = g.assign(xr=g.groupby('entry_date').x.rank(pct=True), yr_=g.groupby('entry_date').y.rank(pct=True))
    within = wd.xr.corr(wd.yr_)
    q = pd.qcut(g.x, 5, labels=False, duplicates='drop')
    rows.append(dict(year=y_, n=len(g), pooled_rank_ic=round(pooled, 3), within_day_rank_ic=round(within, 3),
                     t_pooled=round(pooled * np.sqrt(len(g) - 2), 2),
                     move_bottom_gap_q=round(g.y[q == 0].mean(), 3), move_top_gap_q=round(g.y[q == q.max()].mean(), 3)))
print('\nown gap vs own move to expiry (sigma units):'); print(pd.DataFrame(rows).to_string(index=False), flush=True)

# ── books ──
_orig = ec.gap_drift
def own_drift(C, closes, mkt_gap, gamma=None, fit=None):
    C = C.reset_index(drop=True)
    unit = _orig(C, closes, pd.DataFrame({'date': pd.to_datetime(C.entry_date).dt.normalize().unique(), 'mkt_gap': 0.0}), gamma=1.0, fit=(-1.0, 1.0, 1.0))
    x = pd.MultiIndex.from_arrays([C.ticker, pd.to_datetime(C.entry_date).dt.normalize()]).map(OWN).astype(float).values
    yrs = pd.to_datetime(C.entry_date).dt.year.values
    ks = sorted(FITOWN)
    par = np.array([FITOWN[max([k for k in ks if k <= y] or [None])] if any(k <= y for k in ks) else (0.0, 1.0, 0.0) for y in yrs])
    z = np.clip((x - par[:, 0]) / par[:, 1], -4, 4)
    return np.nan_to_num(par[:, 2] * z * unit)

def metr(sel, ey):
    with contextlib.redirect_stdout(io.StringIO()):
        s = rmc.build_payload(sel.sort_values('entry_date_dt').reset_index(drop=True), ey, '')['summary']
    return dict(n=len(sel), pnl=round(sel.pnl_per_contract.sum()), win=round(100 * (sel._outcome == 'WIN').mean(), 1), sh=s['qty1_sharpe_weekly'], dd=s['qty1_max_dd'])
out = []
for name in ('canon', 'market gap', 'own gap'):
    ec.GAP_GAMMA = 0.0 if name == 'canon' else 1.0
    ec.gap_drift = own_drift if name == 'own gap' else _orig
    for win, ey in (('IS', 2025), ('OOT', 2026)):
        S = rec.select(win); yr = S.entry_date_dt.dt.year
        per = [(str(y), yr == y, y) for y in sorted(yr.unique())] + ([('2021-25', yr >= 2021, 2025), ('IS 2020-25', yr > 0, 2025)] if win == 'IS' else [])
        for lab, m, e in per: out.append(dict(config=name, period=lab, **metr(S[m], e)))
    print(f'evaluated {name}', flush=True)
ec.gap_drift = _orig
X = pd.DataFrame(out); order = ['2020', '2021', '2022', '2023', '2024', '2025', '2021-25', 'IS 2020-25', '2026']
pd.set_option('display.width', 200)
for col in ('pnl', 'sh', 'dd', 'win', 'n'):
    print(f'\n--- {col} ---'); print(X.pivot(index='period', columns='config', values=col).reindex(order)[['canon', 'market gap', 'own gap']].to_string())
X.to_csv('research/ta_direction/ta_gap_own_results.csv', index=False)
