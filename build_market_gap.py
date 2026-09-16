"""Backtest-side market opening gap series for the §0.45 P_real drift -> output/market_gap_backtest.parquet.

Daily OHLC per ent_canon.GAP_UNIVERSE name from data/daily_bars_yahoo/<T>.csv; dates that file lacks (it has
no bars 2026-01-01..05-14) are filled from the Yahoo chart API (read-only HTTP GET; local rows win). The
live ranker does NOT read this file: live/fetch_market_gap.py computes the same series from IBKR bars.

    python3 build_market_gap.py              # local files + Yahoo API fill
    python3 build_market_gap.py --no-api     # local files only
"""
import argparse, json, os, sys, time, urllib.request
import pandas as pd
sys.path.insert(0, '.')
import ent_canon as ec

OUT = 'output/market_gap_backtest.parquet'


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
    bars, filled = [], {}
    for i, t in enumerate(ec.GAP_UNIVERSE, 1):
        y = local(t)
        if not a.no_api:
            try:
                z = api(t); z = z[~z.date.isin(set(y.date))]
                filled[t] = len(z); y = pd.concat([y, z], ignore_index=True)
            except Exception as e:
                print(f'  {t}: Yahoo API failed ({str(e)[:60]}); local bars only', flush=True)
            time.sleep(0.25)
        y['ticker'] = t; bars.append(y)
        if i % 20 == 0 or i == len(ec.GAP_UNIVERSE): print(f'bars {i}/{len(ec.GAP_UNIVERSE)} ({100*i/len(ec.GAP_UNIVERSE):.0f}%)', flush=True)
    mg = ec.market_gap(pd.concat(bars, ignore_index=True))
    if os.path.exists(OUT):
        print(f'{OUT} exists; writing {OUT}.new instead (never overwrite without the user saying so)'); OUT = OUT + '.new'
    mg.to_parquet(OUT, index=False)
    print(f'wrote {OUT}: {len(mg)} dates {mg.date.min().date()}..{mg.date.max().date()}, '
          f'forecast days {int(mg.mkt_gap.notna().sum())}; API rows added {sum(filled.values())}')
