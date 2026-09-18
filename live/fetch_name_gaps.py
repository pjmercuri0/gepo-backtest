"""Today's per-stock opening gaps for the §0.45 own-gap P_real drift -> output/name_gaps_live.parquet (MERGE).

For every equity the live scan covers (live.fetch_ibkr_closes.universe(): config.SP100_TICKERS plus snapshot
symbols, index roots skipped): ~6 months of IBKR daily TRADES bars (RTH). Today's open comes from the in-progress
daily bar; if IBKR returns no bar dated today, from the first 30-minute bar of today. Each stock's gap is computed by
ent_canon.name_gaps, the same function the backtest uses. No cross-stock average is formed anywhere.

    python3 -m live.fetch_name_gaps            # cron 09:35 and 09:50 Mon-Fri; fetches only names missing today
    python3 -m live.fetch_name_gaps --force    # refetch every name and replace today's rows

Read-only connection, client id 179. ~2.5 s per request: 96 names in ~4 min, so a 09:35 start
is done before the 09:45 scan (at 6 s it finished 09:47 and missed it). Backs off 60 s and retries
once on a pacing violation.
The ranker reads this file; a candidate whose stock has no row for today scores with zero drift.
"""
from __future__ import annotations
import argparse, os, sys, time
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import ent_canon as ec
from live import live_config as lc

STORE = Path(os.environ.get('GEPO_NAME_GAPS', ROOT / 'output' / 'name_gaps_live.parquet'))
CLIENT_ID = 179


def load_store() -> pd.DataFrame:
    if STORE.exists():
        d = pd.read_parquet(STORE); d['date'] = pd.to_datetime(d['date']).dt.normalize(); return d
    return pd.DataFrame(columns=['ticker', 'date', 'gap', 'fetched_at'])


def fetch_bars(names: list[str], today: pd.Timestamp, sleep: float) -> pd.DataFrame:
    from ib_insync import IB, Stock
    ib = IB(); ib.connect(lc.IB_HOST, lc.IB_PORT, clientId=CLIENT_ID, readonly=True, timeout=lc.IB_CONNECT_TIMEOUT)
    rows = []
    try:
        for i, s in enumerate(names, 1):
            try:
                c = Stock(s, 'SMART', 'USD')
                if not ib.qualifyContracts(c):
                    print(f'{i:>3} {s:<6} no contract', flush=True); continue
                bars = ib.reqHistoricalData(c, endDateTime='', durationStr='4 M', barSizeSetting='1 day',
                                            whatToShow='TRADES', useRTH=True, formatDate=1)
                b = [{'ticker': s, 'date': pd.Timestamp(x.date).normalize(), 'open': x.open, 'high': x.high, 'low': x.low, 'close': x.close} for x in bars]
                if b and b[-1]['date'] != today:
                    time.sleep(sleep)
                    intr = ib.reqHistoricalData(c, endDateTime='', durationStr='1 D', barSizeSetting='30 mins',
                                                whatToShow='TRADES', useRTH=True, formatDate=1)
                    intr = [x for x in intr if pd.Timestamp(x.date).tz_localize(None).normalize() == today]
                    if intr:   # only today's OPEN enters today's gap
                        b.append({'ticker': s, 'date': today, 'open': intr[0].open, 'high': intr[0].high, 'low': intr[0].low, 'close': intr[-1].close})
                rows += b
                print(f'{i:>3}/{len(names)} {s:<6} {len(b)} bars, last {b[-1]["date"].date() if b else "-"}', flush=True)
            except Exception as e:
                msg = str(e)
                # A pacing violation poisons every later request, so back off hard and
                # retry this one before continuing (2026-09-18, when the interval went
                # 6.0s -> 2.5s to finish before the 09:45 scan instead of 09:47).
                if 'pacing' in msg.lower() or 'max rate' in msg.lower():
                    print(f'{i:>3} {s:<6} PACING -- backing off 60s', flush=True)
                    time.sleep(60)
                    try:
                        bars = ib.reqHistoricalData(c, endDateTime='', durationStr='4 M',
                                                    barSizeSetting='1 day', whatToShow='TRADES',
                                                    useRTH=True, formatDate=1)
                        rows += [{'ticker': s, 'date': pd.Timestamp(x.date).normalize(),
                                  'open': x.open, 'high': x.high, 'low': x.low, 'close': x.close}
                                 for x in bars]
                        print(f'{i:>3}/{len(names)} {s:<6} {len(bars)} bars (after backoff)', flush=True)
                    except Exception as e2:
                        print(f'{i:>3} {s:<6} ERR after backoff {str(e2)[:60]}', flush=True)
                else:
                    print(f'{i:>3} {s:<6} ERR {msg[:80]}', flush=True)
            time.sleep(sleep)
    finally:
        if ib.isConnected():
            ib.disconnect()
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument('--force', action='store_true'); ap.add_argument('--sleep', type=float, default=2.5)
    a = ap.parse_args()
    from live.fetch_ibkr_closes import universe
    today = pd.Timestamp.now(tz='America/New_York').tz_localize(None).normalize()
    cur = load_store()
    have = set() if a.force else set(cur[(cur.date == today) & cur.gap.notna()].ticker)
    names = [n for n in universe() if n not in have]
    if not names:
        print(f'gaps for {today.date()} already stored for all {len(have)} names; nothing to do'); return 0
    bars = fetch_bars(names, today, a.sleep)
    if bars.empty:
        print('ERROR: no bars from IBKR; nothing written'); return 1
    g = ec.name_gaps(bars)
    g = g[(g.date == today) & g.gap.notna()].assign(fetched_at=pd.Timestamp.now(tz='America/New_York').strftime('%Y-%m-%d %H:%M:%S'))
    if g.empty:
        print(f'ERROR: no usable gap for {today.date()} (IBKR returned no bar dated today?); nothing written'); return 1
    keep = cur[~((cur.date == today) & cur.ticker.isin(g.ticker))]
    out = pd.concat([keep, g], ignore_index=True).sort_values(['date', 'ticker'])
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix('.tmp.parquet'); out.to_parquet(tmp, index=False); os.replace(tmp, STORE)
    m_, s_, b_ = ec.gap_fit(today.year); z = (g.gap - m_) / s_
    missing = sorted(set(names) - set(g.ticker))
    print(f'{today.date()}: stored gaps for {len(g)} names (z median {z.median():+.2f}, range {z.min():+.2f}..{z.max():+.2f}); '
          f'no gap for {len(missing)}: {missing[:15]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
