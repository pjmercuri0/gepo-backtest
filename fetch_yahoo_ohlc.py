"""Fetch daily OHLC 2019-11 -> 2026-01 from Yahoo chart API for the backtest
ticker universe. Writes one CSV per ticker to data/daily_bars_yahoo/ (new
directory, nothing overwritten elsewhere). For the Parkinson RV test.
"""
import json, os, time, urllib.request
import pandas as pd

OUT = 'data/daily_bars_yahoo'
os.makedirs(OUT, exist_ok=True)
P1, P2 = 1572566400, 1767225600  # 2019-11-01 .. 2026-01-01
ALIAS = {'SPXW': '^SPX', 'RUTW': '^RUT'}

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
tickers = sorted(df['ticker'].unique())

ok, fail = 0, []
for t in tickers:
    path = f'{OUT}/{t}.csv'
    if os.path.exists(path):
        ok += 1
        continue
    sym = ALIAS.get(t, t)
    url = (f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}'
           f'?period1={P1}&period2={P2}&interval=1d')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.load(urllib.request.urlopen(req, timeout=30))
        r = data['chart']['result'][0]
        q = r['indicators']['quote'][0]
        out = pd.DataFrame({'ts': r['timestamp'], 'open': q['open'], 'high': q['high'],
                            'low': q['low'], 'close': q['close']})
        out['date'] = pd.to_datetime(out['ts'], unit='s', utc=True).dt.tz_convert(
            'America/New_York').dt.date
        out = out.dropna(subset=['high', 'low', 'close'])
        out[['date', 'open', 'high', 'low', 'close']].to_csv(path, index=False)
        ok += 1
        print(f'{t}: {len(out)} bars', flush=True)
    except Exception as e:
        fail.append(t)
        print(f'{t}: FAIL {e}', flush=True)
    time.sleep(0.7)

print(f'\ndone: {ok} ok, failed: {fail}')
