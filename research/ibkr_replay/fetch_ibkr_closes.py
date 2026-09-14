"""Pull N years of IBKR daily TRADES closes for the replay universe.

IBKR-only by design: the replay must not touch output/daily_closes.parquet, which
is vendor-seeded (build_daily_closes.py globs the vendor year files) and Yahoo-
backfilled (§0.36). This writes its own store so the two never mix.

Read-only IBKR connection. Client id 178 avoids the reserved ones (11, 12,
100-109, 110 combo, 193 assign).

    python research/ibkr_replay/fetch_ibkr_closes.py [--years 2] [--out <path>]

Pacing: IBKR allows ~60 historical-data requests per 10 minutes, so this sleeps
6s between tickers. ~10 min for the SP100 universe. Run it once; the replay reads
the parquet.
"""
from __future__ import annotations
import argparse, glob, os, sys, time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from live import live_config as lc

DEFAULT_OUT = ROOT / 'research' / 'ibkr_replay' / 'ibkr_closes.parquet'


def universe() -> list[str]:
    """Every symbol that appears in a stored snapshot, plus the configured list."""
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
    return sorted(s for s in syms if isinstance(s, str) and s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', type=int, default=2)
    ap.add_argument('--out', default=str(DEFAULT_OUT))
    ap.add_argument('--sleep', type=float, default=6.0)
    a = ap.parse_args()

    from ib_insync import IB, Stock
    syms = universe()
    print(f"{len(syms)} tickers, {a.years}y of daily bars, ~{len(syms)*a.sleep/60:.0f} min", flush=True)

    ib = IB()
    ib.connect(lc.IB_HOST, lc.IB_PORT, clientId=178, readonly=True, timeout=30)
    rows, bad = [], []
    try:
        for i, s in enumerate(syms, 1):
            try:
                c = Stock(s, 'SMART', 'USD')
                if not ib.qualifyContracts(c):
                    bad.append((s, 'no contract')); continue
                bars = ib.reqHistoricalData(c, endDateTime='', durationStr=f'{a.years} Y',
                                            barSizeSetting='1 day', whatToShow='TRADES',
                                            useRTH=True, formatDate=1)
                if not bars:
                    bad.append((s, 'no bars')); continue
                rows += [{'ticker': s, 'date': pd.Timestamp(b.date), 'close': float(b.close)}
                         for b in bars]
                print(f"{i:>3}/{len(syms)} {s:<6} {len(bars):>4} bars", flush=True)
            except Exception as e:
                bad.append((s, str(e)[:70])); print(f"{i:>3}/{len(syms)} {s:<6} ERR {e}", flush=True)
            time.sleep(a.sleep)
    finally:
        if ib.isConnected():
            ib.disconnect()

    d = pd.DataFrame(rows)
    if d.empty:
        print("no bars fetched"); return 1
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    d.to_parquet(a.out)
    print(f"\nwrote {a.out}: {len(d):,} rows, {d.ticker.nunique()} tickers, "
          f"{d.date.min().date()} -> {d.date.max().date()}")
    if bad:
        print("failed:", bad)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
