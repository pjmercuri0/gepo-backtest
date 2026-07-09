"""Rescore the frozen live snapshots under the CANONICAL config.

Question: would canon (rv_vs_iv DKL, k=10, thr=0.075, top-5 qualified,
regime off) have taken the picks that bled live in 2026-05/06?

Uses the exact snapshot parquet each frozen day used, with the empirical
window installed AS OF the entry date (no forward window). Settlement from
vendor EOD on expiry. Read-only.
"""
import json, os, sys, glob
import pandas as pd

sys.path.insert(0, '.')
import config as backtest_config
import ground
import empirical_runner as er
from live import ranker

VENDOR_DIRS = {'2026-05': 'data/DG_2026May', '2026-06': 'data/DG_2026June'}
LAST_PCT = 0.80

assert ground.DKL_REFERENCE == 'rv_vs_iv' and ground.DKL_K == 12.0
assert backtest_config.REGIME_FILTER is False

POOL = ranker._POOL if ranker._POOL is not None else er.load_master_pool()


def expiry_closes(expiry, symbols):
    ym = expiry[:7]
    ymd = expiry.replace('-', '')
    out = {}
    for part in (1, 2):
        path = os.path.join(VENDOR_DIRS[ym], f'Greek_{ymd}_OData{part}.csv')
        if not os.path.exists(path):
            continue
        for chunk in pd.read_csv(path, usecols=['Symbol', 'UnderlyingPrice'],
                                 chunksize=500_000, skipinitialspace=True):
            sub = chunk[chunk['Symbol'].isin(symbols)]
            for sym, px in sub.groupby('Symbol')['UnderlyingPrice'].first().items():
                out.setdefault(sym, float(px))
    return out


def pnl_share(credit, ss, ls, settle, width, sp_type):
    if sp_type == 'bull_put':
        intr = min(max(ss - settle, 0.0), width)
        oc = 'WIN' if settle > ss else ('LOSS' if settle <= ls else 'PARTIAL')
    else:
        intr = min(max(settle - ss, 0.0), width)
        oc = 'WIN' if settle < ss else ('LOSS' if settle >= ls else 'PARTIAL')
    return credit - intr, oc


def main():
    rows = []
    for f in sorted(glob.glob('live/frozen/*.json')):
        d = json.load(open(f))
        date = d['data_date']
        snap_path = d.get('snapshot_file')
        if not snap_path or not os.path.exists(snap_path):
            print(f'{date}: snapshot missing ({snap_path}) — skip')
            continue
        res = (d.get('outcome') or {}).get('results', {})
        live_picks = {(p['ticker'], float(p['short_strike'])) for p in d.get('top_picks', [])}

        ok = er.install_window(POOL, pd.Timestamp(date))
        print(f'\n===== {date} (window asof {date}, installed={ok}) =====', flush=True)
        df = pd.read_parquet(snap_path)
        ranked = ranker.rank_snapshot(df)
        if ranked.empty:
            print(f'{date}: no ranked candidates')
            continue
        canon = ranked[ranked['qualified']].head(5)
        print(f'  canon qualified top-5: {len(canon)} (live took {len(live_picks)})')

        if canon.empty:
            continue
        expiry = pd.Timestamp(canon.iloc[0]['expiry_date']).strftime('%Y-%m-%d')
        settles = expiry_closes(expiry, set(canon['ticker']))
        for _, r in canon.iterrows():
            settle = settles.get(r['ticker'])
            credit = LAST_PCT * float(r['net_credit'])
            width = float(r['spread_width'])
            if settle is None:
                pnl, oc = None, 'OPEN/NA'
            else:
                pnl, oc = pnl_share(credit, float(r['short_strike']),
                                    float(r['long_strike']), settle, width, r['spread_type'])
                pnl *= 100
            overlap = (r['ticker'], float(r['short_strike'])) in live_picks
            rows.append({'date': date, 'ticker': r['ticker'], 'type': r['spread_type'],
                         'ss': r['short_strike'], 'ls': r['long_strike'],
                         'GROUND': round(float(r['GROUND']), 4),
                         'net_credit': float(r['net_credit']), 'settle': settle,
                         'oc': oc, 'pnl': None if pnl is None else round(pnl, 2),
                         'same_as_live': overlap})

    out = pd.DataFrame(rows)
    pd.set_option('display.width', 220)
    print('\n================ CANON PICKS ================')
    print(out.to_string(index=False))
    settled = out.dropna(subset=['pnl'])
    if len(settled):
        print(f'\ncanon: {len(settled)} settled picks  total ${settled.pnl.sum():.2f}  '
              f'avg ${settled.pnl.mean():.2f}  '
              f'WR {(settled.oc=="WIN").mean()*100:.1f}%  '
              f'overlap with live picks: {settled.same_as_live.sum()}/{len(settled)}')
    out.to_csv('/tmp/rescore_snapshots_canon.csv', index=False)
    print('wrote /tmp/rescore_snapshots_canon.csv')


if __name__ == '__main__':
    main()
