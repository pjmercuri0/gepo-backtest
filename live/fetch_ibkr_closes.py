"""Pull official daily TRADES closes from IBKR into output/ibkr_closes.parquet (the ONLY close
series the live ranker reads for P_real — live/closes.py, 2026-09-15).

    python3 -m live.fetch_ibkr_closes --years 2        # one-time seed on the Mac mini, ~10 min
    python3 -m live.fetch_ibkr_closes --days 10        # daily top-up (cron_daily_bars.sh, 17:01)

MERGE only: rows already in the store are kept, new (ticker, date) rows are appended. A bar
IBKR has already closed does not change, so "existing wins" is safe. Read-only connection,
client id 178 (avoids 11/12 defaults, 100-109 fetchers, 110 combo, 193 assign).

Universe: config.SP100_TICKERS plus every equity symbol seen in a stored snapshot. Index
roots (live_config.LIVE_INDEX_ROOTS) are skipped: they are not stocks and P_real is not
computed for them. SPY is included so the store carries the session calendar even if a few
equities have gaps.

Pacing: IBKR allows ~60 historical-data requests per 10 minutes -> 6 s between tickers.
"""
from __future__ import annotations
import argparse, glob, sys, time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live import live_config as lc
from live.closes import IBKR_STORE, append_closes, closes_status

CLIENT_ID = 178


def universe() -> list[str]:
    syms: set[str] = set()
    for f in sorted(glob.glob(str(ROOT / 'live' / 'snapshots' / '2*' / '*.parquet'))):
        try:
            syms |= set(pd.read_parquet(f, columns=['Symbol']).Symbol.unique())
        except Exception:
            continue
    try:
        import config as bt_config
        syms |= set(getattr(bt_config, 'SP100_TICKERS', []))
    except Exception:
        pass
    skip = set(getattr(lc, 'LIVE_INDEX_ROOTS', {}).keys()) | {'SPXW', 'RUTW', 'MMC'}
    syms.add('SPY')
    return sorted(s for s in syms if isinstance(s, str) and s and s not in skip)


def fetch(years: int | None, days: int | None, sleep: float, syms: list[str]) -> tuple[pd.DataFrame, list]:
    from ib_insync import IB, Stock
    duration = f'{years} Y' if years else f'{days} D'
    ib = IB()
    ib.connect(lc.IB_HOST, lc.IB_PORT, clientId=CLIENT_ID, readonly=True, timeout=lc.IB_CONNECT_TIMEOUT)
    rows, bad = [], []
    try:
        for i, s in enumerate(syms, 1):
            try:
                c = Stock(s, 'SMART', 'USD')
                if not ib.qualifyContracts(c):
                    bad.append((s, 'no contract')); continue
                bars = ib.reqHistoricalData(c, endDateTime='', durationStr=duration, barSizeSetting='1 day',
                                            whatToShow='TRADES', useRTH=True, formatDate=1)
                if not bars:
                    bad.append((s, 'no bars')); continue
                rows += [{'ticker': s, 'date': pd.Timestamp(b.date), 'close': float(b.close)} for b in bars]
                print(f'{i:>3}/{len(syms)} {s:<6} {len(bars):>4} bars', flush=True)
            except Exception as e:
                bad.append((s, str(e)[:70])); print(f'{i:>3}/{len(syms)} {s:<6} ERR {e}', flush=True)
            time.sleep(sleep)
    finally:
        if ib.isConnected():
            ib.disconnect()
    return pd.DataFrame(rows), bad


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--years', type=int, default=None, help='seed: N years of daily bars per ticker')
    g.add_argument('--days', type=int, default=None, help='top-up: last N days (default 10)')
    ap.add_argument('--sleep', type=float, default=6.0)
    ap.add_argument('--tickers', default=None, help='comma list to restrict (testing)')
    a = ap.parse_args()
    if a.years is None and a.days is None:
        a.days = 10
    syms = a.tickers.split(',') if a.tickers else universe()
    print(f"{len(syms)} tickers, {'%d y' % a.years if a.years else '%d d' % a.days} of daily bars, "
          f"~{len(syms) * a.sleep / 60:.0f} min; store {IBKR_STORE}", flush=True)
    before = closes_status()
    d, bad = fetch(a.years, a.days, a.sleep, syms)
    if d.empty:
        print('no bars fetched'); return 1
    d = d[d.close > 0]
    # a bar dated today before the close is a partial session: only keep it after 16:05 ET
    now = pd.Timestamp.now(tz='America/New_York')
    today = now.normalize().tz_localize(None)
    if (d.date == today).any() and (now.hour, now.minute) < (16, 5):
        d = d[d.date != today]
        print('dropped today\'s partial bar (before 16:05 ET)', flush=True)
    added = append_closes(d, store=IBKR_STORE)
    after = closes_status()
    print(f'\nfetched {len(d):,} bars / {d.ticker.nunique()} tickers, {d.date.min().date()} -> {d.date.max().date()}; '
          f'store +{added:,} rows: {before["rows"]:,} -> {after["rows"]:,}, {after["tickers"]} tickers, '
          f'{after["first"]} -> {after["last"]}, {after["sessions"]} sessions')
    if bad:
        print('failed:', bad)
    return 0


if __name__ == '__main__':
    sys.exit(main())
