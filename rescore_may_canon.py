"""Canon rescore of May 2026 snapshots, with LastPrice backfilled from vendor EOD.

May snapshots predate the fetcher's LastPrice column; spreads.build_candidates
drops legs without LAST. We merge the vendor EOD LAST (same day, 4pm) onto the
15:45 snapshot rows. Caveat: hybrid timing (EOD LAST clamped to 15:45 BBO).
Read-only on data.
"""
import json, os, sys, glob
import pandas as pd

sys.path.insert(0, '.')
import empirical_runner as er
from live import ranker

LAST_PCT = 0.80
POOL = ranker._POOL

USECOLS = ['Symbol', 'ExpirationDate', 'StrikePrice', 'PutCall', 'LastPrice', 'UnderlyingPrice']


def vendor_df(date):
    ymd = date.replace('-', '')
    frames = []
    for part in (1, 2):
        p = f'data/DG_2026May/Greek_{ymd}_OData{part}.csv'
        if os.path.exists(p):
            frames.append(pd.read_csv(p, usecols=USECOLS, skipinitialspace=True))
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
    return credit - intr, oc


rows = []
for f in sorted(glob.glob('live/frozen/2026-05-*.json')):
    d = json.load(open(f))
    date = d['data_date']
    sp = d.get('snapshot_file')
    live_picks = {(p['ticker'], float(p['short_strike'])) for p in d.get('top_picks', [])}
    snap = pd.read_parquet(sp)
    snap['StrikePrice'] = snap['StrikePrice'].astype(float).round(3)
    v = vendor_df(date)
    snap = snap.merge(v[['Symbol', 'ExpirationDate', 'StrikePrice', 'PutCall', 'LastPrice']],
                      on=['Symbol', 'ExpirationDate', 'StrikePrice', 'PutCall'], how='left')
    cov = snap['LastPrice'].notna().mean()
    er.install_window(POOL, pd.Timestamp(date))
    ranked = ranker.rank_snapshot(snap)
    nq = 0 if ranked.empty else int(ranked['qualified'].sum())
    print(f'===== {date}: LAST coverage {cov*100:.0f}%, '
          f'candidates {len(ranked)}, qualified {nq}', flush=True)
    if ranked.empty:
        continue
    canon = ranked[ranked['qualified']].head(5)
    if canon.empty:
        continue
    expiry = pd.Timestamp(canon.iloc[0]['expiry_date']).strftime('%Y-%m-%d')
    settles = (vendor_df(expiry).groupby('Symbol')['UnderlyingPrice'].first().to_dict()
               if os.path.exists(f"data/DG_2026May/Greek_{expiry.replace('-','')}_OData1.csv") else {})
    for _, r in canon.iterrows():
        settle = settles.get(r['ticker'])
        credit = LAST_PCT * float(r['net_credit'])
        if settle is None:
            pnl, oc = None, 'NA'
        else:
            pnl, oc = pnl_share(credit, float(r['short_strike']), float(r['long_strike']),
                                float(settle), float(r['spread_width']), r['spread_type'])
            pnl *= 100
        rows.append({'date': date, 'ticker': r['ticker'], 'ss': r['short_strike'],
                     'ls': r['long_strike'], 'GROUND': round(float(r['GROUND']), 4),
                     'net_credit': float(r['net_credit']), 'settle': settle, 'oc': oc,
                     'pnl': None if pnl is None else round(pnl, 2),
                     'same_as_live': (r['ticker'], float(r['short_strike'])) in live_picks})

out = pd.DataFrame(rows)
pd.set_option('display.width', 220)
print('\n========= CANON PICKS (May, vendor-LAST backfill) =========')
print(out.to_string(index=False) if len(out) else '(none)')
s = out.dropna(subset=['pnl']) if len(out) else out
if len(s):
    print(f'\ncanon May: {len(s)} settled  total ${s.pnl.sum():.2f}  avg ${s.pnl.mean():.2f}  '
          f'WR {(s.oc=="WIN").mean()*100:.1f}%  overlap {s.same_as_live.sum()}/{len(s)}')
out.to_csv('/tmp/rescore_may_canon.csv', index=False)
