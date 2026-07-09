"""Acid test: two-channel GROUND (k2*DKL_quote) on the frozen live snapshots.

May days: vendor EOD LAST backfilled onto the 15:45 snapshot (as in
rescore_may_canon.py). June days: snapshot LAST as-is. For each day, rank
with the live ranker, add DKL_quote = Bernoulli KL between net_credit/width
and mid_credit/width, penalize GROUND by exp(-k2*DKL_quote), take top-5
above thr=0.075, settle on vendor expiry close. Read-only.
"""
import json, os, sys, glob, math
import numpy as np
import pandas as pd

sys.path.insert(0, '.')
import empirical_runner as er
from live import ranker

LAST_PCT = 0.80
THR = 0.075
K2_GRID = [0, 1, 2, 5]
POOL = ranker._POOL if ranker._POOL is not None else er.load_master_pool()
VENDOR_DIRS = {'2026-05': 'data/DG_2026May', '2026-06': 'data/DG_2026June'}
USECOLS = ['Symbol', 'ExpirationDate', 'StrikePrice', 'PutCall', 'LastPrice', 'UnderlyingPrice']


def vendor_df(date):
    ymd = date.replace('-', '')
    frames = []
    for part in (1, 2):
        p = os.path.join(VENDOR_DIRS[date[:7]], f'Greek_{ymd}_OData{part}.csv')
        if os.path.exists(p):
            frames.append(pd.read_csv(p, usecols=USECOLS, skipinitialspace=True))
    if not frames:
        return None
    v = pd.concat(frames, ignore_index=True)
    v['ExpirationDate'] = pd.to_datetime(v['ExpirationDate'])
    v['StrikePrice'] = v['StrikePrice'].astype(float).round(3)
    return v


def pnl_share(credit, ss, ls, settle, width, sp_type):
    if sp_type == 'bull_put':
        intr = min(max(ss - settle, 0.0), width)
        oc = 'WIN' if settle > ss else ('LOSS' if settle <= ls else 'PARTIAL')
    else:
        intr = min(max(settle - ss, 0.0), width)
        oc = 'WIN' if settle < ss else ('LOSS' if settle >= ls else 'PARTIAL')
    pnl = credit - intr
    if oc == 'PARTIAL' and pnl > 0:
        pnl *= 0.5
    return pnl, oc


EPS = 1e-4
ranked_days = {}
for f in sorted(glob.glob('live/frozen/*.json')):
    d = json.load(open(f))
    date = d['data_date']
    sp = d.get('snapshot_file')
    if not sp or not os.path.exists(sp):
        continue
    snap = pd.read_parquet(sp)
    if date[:7] == '2026-05':
        snap['StrikePrice'] = snap['StrikePrice'].astype(float).round(3)
        v = vendor_df(date)
        if 'LastPrice' in snap.columns:
            snap = snap.drop(columns=['LastPrice'])
        snap = snap.merge(v[['Symbol', 'ExpirationDate', 'StrikePrice', 'PutCall', 'LastPrice']],
                          on=['Symbol', 'ExpirationDate', 'StrikePrice', 'PutCall'], how='left')
    er.install_window(POOL, pd.Timestamp(date))
    ranked = ranker.rank_snapshot(snap)
    if ranked.empty:
        continue
    c_last = ranked['net_credit'].astype(float)
    c_mid = (ranked['short_mid'].astype(float) - ranked['long_mid'].astype(float))
    w = ranked['spread_width'].astype(float)
    q1 = np.clip(c_last / w, EPS, 1 - EPS)
    q2 = np.clip(c_mid / w, EPS, 1 - EPS)
    ranked = ranked.copy()
    ranked['DKL_quote'] = q1 * np.log(q1 / q2) + (1 - q1) * np.log((1 - q1) / (1 - q2))
    ranked_days[date] = ranked

print(f'ranked {len(ranked_days)} frozen days')

for k2 in K2_GRID:
    rows = []
    for date, ranked in sorted(ranked_days.items()):
        r2 = ranked.copy()
        r2['SCORE'] = r2['GROUND'] * np.exp(-k2 * r2['DKL_quote'])
        picks = r2[r2['SCORE'] >= THR].sort_values('SCORE', ascending=False).head(5)
        if picks.empty:
            continue
        expiry = pd.Timestamp(picks.iloc[0]['expiry_date']).strftime('%Y-%m-%d')
        v_exp = vendor_df(expiry)
        settles = ({} if v_exp is None else
                   v_exp.groupby('Symbol')['UnderlyingPrice'].first().to_dict())
        for _, r in picks.iterrows():
            settle = settles.get(r['ticker'])
            credit = LAST_PCT * float(r['net_credit'])
            if settle is None:
                continue
            pnl, oc = pnl_share(credit, float(r['short_strike']), float(r['long_strike']),
                                float(settle), float(r['spread_width']), r['spread_type'])
            rows.append({'date': date, 'month': date[:7], 'ticker': r['ticker'],
                         'ss': float(r['short_strike']), 'GROUND': float(r['GROUND']),
                         'DKLq': float(r['DKL_quote']), 'oc': oc, 'pnl': pnl * 100})
    out = pd.DataFrame(rows)
    if out.empty:
        print(f'k2={k2}: no settled picks')
        continue
    line = f'k2={k2:<3}'
    for month, g in out.groupby('month'):
        line += (f'  | {month}: n={len(g)} avg=${g.pnl.mean():7.2f} '
                 f'WR={(g.oc=="WIN").mean()*100:4.1f}%')
    line += f'  | ALL: n={len(out)} avg=${out.pnl.mean():7.2f}'
    print(line)
    if k2 == 2:
        pd.set_option('display.width', 200)
        print('\n--- k2=2 per-pick detail ---')
        print(out.round(3).to_string(index=False))
        print()
