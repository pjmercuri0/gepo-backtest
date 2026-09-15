"""Daily underlying closes for the realized belief (P_real). IBKR-ONLY (2026-09-15).

The live ranker must not depend on the vendor year files (they are not on the Mac mini and
lag by a day). load_closes() therefore reads:

  1. output/ibkr_closes.parquet — official daily TRADES closes pulled from IBKR by
     live/fetch_ibkr_closes.py (seed: --years 2; daily top-up from cron_daily_bars.sh).
     IBKR returns SESSIONS ONLY, so the vendor's holiday republishes (stale duplicate
     closes on ~9 non-session dates a year) never enter this series. On the in-sample
     backtest those duplicates cost 0.13 of weekly Sharpe (1.20 -> 1.07); see the handoff.
  2. live/snapshots/<date>/*.parquet — the LAST snapshot of each day supplies that day's
     price for every symbol it holds, but ONLY for dates AFTER the IBKR store's last date
     (a stop-gap for a missed 16:31 top-up; the next top-up replaces those rows).

Gaps: a ticker missing a session that the rest of the universe traded (an IBKR hiccup,
not a holiday) is forward-filled from its previous close, at most GAP_FFILL_MAX sessions,
so every name shares one session calendar and d-day moves line up. Holidays are not
sessions and are never filled.

The vendor-seeded store output/daily_closes.parquet is still written by
build_daily_closes.py for the backtest-side tools; load_closes() no longer reads it.
"""
from __future__ import annotations
import glob
import os
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / 'output' / 'daily_closes.parquet'            # vendor-seeded (backtest side only)
IBKR_STORE = Path(os.environ.get('GEPO_IBKR_CLOSES', ROOT / 'output' / 'ibkr_closes.parquet'))
SNAPS = ROOT / 'live' / 'snapshots'
CALENDAR_MIN_FRAC = 0.5     # a date is a session if at least this fraction of tickers has an IBKR bar
GAP_FFILL_MAX = 3           # forward-fill a ticker's missing sessions up to this many in a row


def _read(path: Path) -> pd.DataFrame:
    if path.exists():
        d = pd.read_parquet(path)
        d['date'] = pd.to_datetime(d['date']).dt.normalize()
        return d[['ticker', 'date', 'close']]
    return pd.DataFrame(columns=['ticker', 'date', 'close'])


def _from_store() -> pd.DataFrame:
    """The vendor-seeded store (backtest side). Not used by load_closes()."""
    return _read(STORE)


def _from_ibkr() -> pd.DataFrame:
    return _read(IBKR_STORE)


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


def session_calendar(closes: pd.DataFrame) -> pd.DatetimeIndex:
    """Dates on which at least CALENDAR_MIN_FRAC of the tickers have a bar. With an IBKR
    series this is exactly the exchange session calendar; a single ticker's outage cannot
    create or remove a session."""
    if closes.empty:
        return pd.DatetimeIndex([])
    n_tk = closes.ticker.nunique()
    per_date = closes.groupby('date').ticker.nunique()
    return pd.DatetimeIndex(per_date[per_date >= CALENDAR_MIN_FRAC * n_tk].index).sort_values()


def fill_gaps(closes: pd.DataFrame, cal: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """Forward-fill each ticker's missing SESSIONS (dates in cal within the ticker's own
    first..last range), at most GAP_FFILL_MAX in a row. Never adds non-session dates."""
    if closes.empty:
        return closes
    if cal is None:
        cal = session_calendar(closes)
    out = []
    for tk, g in closes.groupby('ticker'):
        g = g.drop_duplicates('date').set_index('date').close.sort_index()
        rng = cal[(cal >= g.index.min()) & (cal <= g.index.max())]
        r = g.reindex(g.index.union(rng)).ffill(limit=GAP_FFILL_MAX)
        r = r[r.index.isin(rng) | r.index.isin(g.index)].dropna()
        out.append(pd.DataFrame({'ticker': tk, 'date': r.index, 'close': r.values}))
    return pd.concat(out, ignore_index=True).sort_values(['ticker', 'date']).reset_index(drop=True)


def load_closes() -> pd.DataFrame:
    """IBKR store + snapshot prints for dates after it; gap-filled on the IBKR session calendar."""
    ib = _from_ibkr()
    last = ib['date'].max() if len(ib) else None
    snap = _from_snapshots(since=last)
    allc = pd.concat([ib.assign(_src=0), snap.assign(_src=1)], ignore_index=True)
    allc = allc.sort_values(['ticker', 'date', '_src']).drop_duplicates(['ticker', 'date'], keep='first')
    allc = allc[['ticker', 'date', 'close']].reset_index(drop=True)
    return fill_gaps(allc, session_calendar(ib) if len(ib) else None)


def closes_status() -> dict:
    """For the ranker's log line: how deep the IBKR store is and where it ends."""
    ib = _from_ibkr()
    if ib.empty:
        return {'rows': 0, 'tickers': 0, 'first': None, 'last': None, 'sessions': 0}
    return {'rows': int(len(ib)), 'tickers': int(ib.ticker.nunique()), 'first': ib.date.min().date(),
            'last': ib.date.max().date(), 'sessions': int(ib.date.nunique())}


def append_closes(new: pd.DataFrame, store: Path = STORE) -> int:
    """MERGE new (ticker, date, close) rows into `store`. Existing rows win. Returns rows added.
    Default store is the vendor-seeded one (build_daily_closes.py); pass IBKR_STORE for IBKR bars."""
    new = new[['ticker', 'date', 'close']].copy(); new['date'] = pd.to_datetime(new['date']).dt.normalize()
    cur = _read(store)
    merged = pd.concat([cur, new], ignore_index=True).drop_duplicates(['ticker', 'date'], keep='first')
    added = len(merged) - len(cur)
    if added > 0:
        store.parent.mkdir(parents=True, exist_ok=True)
        tmp = store.with_suffix('.tmp.parquet'); merged.sort_values(['ticker', 'date']).to_parquet(tmp); os.replace(tmp, store)
    return added
