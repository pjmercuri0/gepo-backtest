"""Daily underlying closes for the realized belief (P_real).

Sources, merged (never wiped):
  1. output/daily_closes.parquet — seeded from the vendor year files by build_daily_closes.py
     (UnderlyingPrice per Symbol/DataDate is the vendor's end-of-day price).
  2. live/snapshots/<date>/*.parquet — the LAST snapshot of each day supplies that day's close
     for every symbol it holds (15:31/15:45 scans; a fair proxy for the close).
  3. IBKR daily bars written by live/fetch_daily_bars.py into the same parquet (append path).

load_closes() returns a DataFrame ticker/date/close, deduplicated with the vendor close
preferred where both exist.
"""
from __future__ import annotations
import glob
import os
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / 'output' / 'daily_closes.parquet'
SNAPS = ROOT / 'live' / 'snapshots'


def _from_store() -> pd.DataFrame:
    if STORE.exists():
        d = pd.read_parquet(STORE)
        d['date'] = pd.to_datetime(d['date']).dt.normalize()
        return d[['ticker', 'date', 'close']]
    return pd.DataFrame(columns=['ticker', 'date', 'close'])


def _from_snapshots(since: pd.Timestamp | None = None) -> pd.DataFrame:
    rows = []
    for day in sorted(glob.glob(str(SNAPS / '2*'))):
        if not os.path.isdir(day):
            continue
        dt = pd.Timestamp(os.path.basename(day))
        if since is not None and dt <= since:
            continue
        files = sorted(glob.glob(os.path.join(day, '*.parquet')))
        if not files:
            continue
        try:
            s = pd.read_parquet(files[-1], columns=['Symbol', 'UnderlyingPrice'])
        except Exception:
            continue
        s = s[s.UnderlyingPrice > 0].groupby('Symbol', as_index=False)['UnderlyingPrice'].first()
        s = s.rename(columns={'Symbol': 'ticker', 'UnderlyingPrice': 'close'}); s['date'] = dt
        rows.append(s[['ticker', 'date', 'close']])
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=['ticker', 'date', 'close'])


def load_closes() -> pd.DataFrame:
    store = _from_store()
    last = store['date'].max() if len(store) else None
    snap = _from_snapshots(since=None)
    allc = pd.concat([store.assign(_src=0), snap.assign(_src=1)], ignore_index=True)
    allc = allc.sort_values(['ticker', 'date', '_src']).drop_duplicates(['ticker', 'date'], keep='first')
    return allc[['ticker', 'date', 'close']].reset_index(drop=True)


def append_closes(new: pd.DataFrame) -> int:
    """MERGE new (ticker, date, close) rows into the store. Existing rows win. Returns rows added."""
    new = new[['ticker', 'date', 'close']].copy(); new['date'] = pd.to_datetime(new['date']).dt.normalize()
    cur = _from_store()
    merged = pd.concat([cur, new], ignore_index=True).drop_duplicates(['ticker', 'date'], keep='first')
    added = len(merged) - len(cur)
    if added > 0:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STORE.with_suffix('.tmp.parquet'); merged.sort_values(['ticker', 'date']).to_parquet(tmp); os.replace(tmp, STORE)
    return added
