"""Today's market opening gap for the §0.45 P_real drift -> output/market_gap_live.parquet (MERGE, never wipe).

For every ent_canon.GAP_UNIVERSE name: ~6 months of IBKR daily TRADES bars (RTH). Today's open comes from the
in-progress daily bar; if IBKR does not return a bar dated today, from the first 30-minute bar of today. The
gap and the cross-name mean are computed by ent_canon.market_gap, the same function the backtest uses.

    python3 -m live.fetch_market_gap            # cron 09:36 and 10:06 Mon-Fri; skips if today is already stored
    python3 -m live.fetch_market_gap --force    # recompute today and replace today's row only

Read-only connection, client id 179. Pacing ~6 s per request (IBKR: ~60 historical requests / 10 min).
The ranker reads this file; with no row for today it scores with zero drift and logs a WARNING.
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

STORE = Path(os.environ.get('GEPO_MARKET_GAP', ROOT / 'output' / 'market_gap_live.parquet'))
CLIENT_ID = 179


def load_store() -> pd.DataFrame:
    if STORE.exists():
        d = pd.read_parquet(STORE); d['date'] = pd.to_datetime(d['date']).dt.normalize(); return d
    return pd.DataFrame(columns=['date', 'mkt_gap', 'n_names', 'fetched_at'])


def fetch_bars(today: pd.Timestamp, sleep: float) -> pd.DataFrame:
    from ib_insync import IB, Stock
    ib = IB(); ib.connect(lc.IB_HOST, lc.IB_PORT, clientId=CLIENT_ID, readonly=True, timeout=lc.IB_CONNECT_TIMEOUT)
    rows = []
    try:
        for i, s in enumerate(ec.GAP_UNIVERSE, 1):
            try:
                c = Stock(s, 'SMART', 'USD')
                if not ib.qualifyContracts(c):
                    print(f'{i:>3} {s:<6} no contract', flush=True); continue
                bars = ib.reqHistoricalData(c, endDateTime='', durationStr='6 M', barSizeSetting='1 day',
                                            whatToShow='TRADES', useRTH=True, formatDate=1)
                b = [{'ticker': s, 'date': pd.Timestamp(x.date).normalize(), 'open': x.open, 'high': x.high, 'low': x.low, 'close': x.close} for x in bars]
                if b and b[-1]['date'] != today:
                    time.sleep(sleep)
                    intr = ib.reqHistoricalData(c, endDateTime='', durationStr='1 D', barSizeSetting='30 mins',
                                                whatToShow='TRADES', useRTH=True, formatDate=1)
                    intr = [x for x in intr if pd.Timestamp(x.date).tz_localize(None).normalize() == today]
                    if intr:   # only today's OPEN is used; H/L/C of today do not enter today's gap
                        b.append({'ticker': s, 'date': today, 'open': intr[0].open, 'high': intr[0].high, 'low': intr[0].low, 'close': intr[-1].close})
                rows += b
                print(f'{i:>3}/{len(ec.GAP_UNIVERSE)} {s:<6} {len(b)} bars, last {b[-1]["date"].date() if b else "-"}', flush=True)
            except Exception as e:
                print(f'{i:>3} {s:<6} ERR {str(e)[:80]}', flush=True)
            time.sleep(sleep)
    finally:
        if ib.isConnected():
            ib.disconnect()
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument('--force', action='store_true'); ap.add_argument('--sleep', type=float, default=6.0)
    a = ap.parse_args()
    today = pd.Timestamp.now(tz='America/New_York').tz_localize(None).normalize()
    cur = load_store()
    if not a.force and (cur.date == today).any() and cur.loc[cur.date == today, 'mkt_gap'].notna().any():
        print(f'market gap for {today.date()} already stored; nothing to do'); return 0
    bars = fetch_bars(today, a.sleep)
    if bars.empty:
        print('ERROR: no bars from IBKR; nothing written'); return 1
    mg = ec.market_gap(bars)
    row = mg[mg.date == today]
    if row.empty or not row.mkt_gap.notna().any():
        n = int(row.n_names.iloc[0]) if len(row) else 0
        print(f'ERROR: no usable market gap for {today.date()} ({n} names with a gap, need {ec.GAP_MIN_NAMES}); nothing written'); return 1
    row = row.assign(fetched_at=pd.Timestamp.now(tz='America/New_York').strftime('%Y-%m-%d %H:%M:%S'))
    m_, s_, b_ = ec.gap_fit(today.year); z = (float(row.mkt_gap.iloc[0]) - m_) / s_
    out = pd.concat([cur[cur.date != today], row], ignore_index=True).sort_values('date')
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix('.tmp.parquet'); out.to_parquet(tmp, index=False); os.replace(tmp, STORE)
    print(f'{today.date()}: mkt_gap {row.mkt_gap.iloc[0]:+.4f} over {int(row.n_names.iloc[0])} names, z {z:+.2f} -> stored in {STORE}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
