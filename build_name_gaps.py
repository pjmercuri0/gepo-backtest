"""Backtest-side per-name opening gaps for the §0.45 own-gap P_real drift -> output/name_gaps_backtest.parquet.

Daily OHLC for every ticker in the research frame (report_ent_canon.FRAME) from data/daily_bars_yahoo/<T>.csv; dates
that file lacks (it has no bars 2026-01-01..05-14) are filled from the Yahoo chart API (read-only GET; local rows
win). The live ranker does NOT read this file: live/fetch_name_gaps.py computes the same gaps from IBKR bars.

    python3 build_name_gaps.py              # local files + Yahoo API fill
    python3 build_name_gaps.py --no-api     # local files only
"""
import argparse, json, os, sys, time, urllib.request
import pandas as pd
sys.path.insert(0, '.')
import ent_canon as ec, report_ent_canon as rec

OUT = 'output/name_gaps_backtest.parquet'


def local(t):
    f = f'data/daily_bars_yahoo/{t}.csv'
    if not os.path.exists(f):
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close'])
    y = pd.read_csv(f); y.columns = [c.lower() for c in y.columns]
    y['date'] = pd.to_datetime(y['date']).dt.normalize()
    return y[['date', 'open', 'high', 'low', 'close']]


def api(t, start='2019-11-01'):
    p1, p2 = int(pd.Timestamp(start).timestamp()), int(pd.Timestamp.now().timestamp())
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{t.replace(".", "-")}?period1={p1}&period2={p2}&interval=1d'
    r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30))['chart']['result'][0]
    q = r['indicators']['quote'][0]
    d = pd.to_datetime(r['timestamp'], unit='s').tz_localize('UTC').tz_convert('America/New_York').normalize().tz_localize(None)
    return pd.DataFrame({'date': d, 'open': q['open'], 'high': q['high'], 'low': q['low'], 'close': q['close']})


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--no-api', action='store_true'); a = ap.parse_args()
    names = sorted(pd.read_parquet(rec.FRAME, columns=['ticker']).ticker.unique())
    bars, added = [], 0
    for i, t in enumerate(names, 1):
        y = local(t)
        if not a.no_api:
            try:
                z = api(t); z = z[~z.date.isin(set(y.date))]
                if len(z): added += len(z); y = pd.concat([y, z], ignore_index=True)
            except Exception as e:
                print(f'  {t}: Yahoo API failed ({str(e)[:60]}); local bars only', flush=True)
            time.sleep(0.25)
        y['ticker'] = t; bars.append(y)
        if i % 20 == 0 or i == len(names): print(f'bars {i}/{len(names)} ({100*i/len(names):.0f}%)', flush=True)
    g = ec.name_gaps(pd.concat(bars, ignore_index=True))
    out = OUT
    if os.path.exists(out):
        out = OUT + '.new'; print(f'{OUT} exists; writing {out} instead (never overwrite without the user saying so)')
    g.to_parquet(out, index=False)
    print(f'wrote {out}: {len(g):,} rows, {g.ticker.nunique()} names, {g.date.min().date()}..{g.date.max().date()}; API rows added {added:,}')
