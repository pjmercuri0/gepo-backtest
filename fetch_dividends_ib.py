"""Pull ex-dividend dates from IBKR instead of NASDAQ's public calendar.

fetch_dividends.py scrapes api.nasdaq.com/api/calendar/dividends. That feed is
badly incomplete for large caps: on 2026-09-03 it returned 29 rows for the day
and HD was not among them, even though IBKR had already messaged the account
that HD goes ex-dividend that day for USD 2.33. The resulting
data/dividend_calendar.csv held 12 symbols out of a 94-ticker universe, so the
ranker's ex-dividend gate was blind on most of the book — including HD, DE,
AMGN, CL, SBUX and DIS, all with positions open into the 2026-09-04 expiry.

IB publishes dividends on generic tick 456: past 12 months, next 12 months,
next ex-date, next amount. It is the same broker that would assign you, it
covers every ticker we trade, and we already hold a Gateway connection.

RUN THIS DAILY. IB's nextDate rolls forward to the FOLLOWING dividend once the
current ex-date arrives — at 06:43 on 2026-09-03, HD already reported
2026-12-04. A daily run accumulates each date while it is still in the future,
which is what the gate needs anyway.

MERGES into data/dividend_calendar.csv, never wipes it (see the HARD RULE in
SESSION_HANDOFF.md): existing rows are preserved and new (Symbol, date) pairs
are added. Read-only IB connection; this never places an order.

Usage:  python3 fetch_dividends_ib.py [--client-id N] [--batch N] [--wait SEC]
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile

from ib_insync import IB, Stock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from live import live_config
from live.fetcher import _connect_with_retry

OUT_PATH = os.path.join(config.DATA_DIR, "dividend_calendar.csv")
FIELDS = ["Symbol", "ExDividendDate", "Amount"]


def _read_existing(path: str) -> dict:
    """Existing rows keyed by (symbol, date). Missing file is not an error."""
    out = {}
    try:
        with open(path) as f:
            for row in csv.DictReader(f):
                sym = (row.get("Symbol") or "").strip()
                date = (row.get("ExDividendDate") or "").strip()
                if sym and date:
                    out[(sym, date)] = {
                        "Symbol": sym,
                        "ExDividendDate": date,
                        "Amount": (row.get("Amount") or "").strip(),
                    }
    except FileNotFoundError:
        pass
    return out


def _atomic_write(path: str, rows: list) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fetch(ib: IB, symbols: list, batch: int, wait: float) -> dict:
    """{symbol: (ex_date, amount)} for symbols IB reports a next dividend for."""
    found = {}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        contracts = [Stock(s, "SMART", "USD") for s in chunk]
        try:
            ib.qualifyContracts(*contracts)
        except Exception as e:
            print(f"  qualify failed for {chunk[0]}..{chunk[-1]}: {e}", flush=True)
            continue
        tickers = [ib.reqMktData(c, "456", False, False) for c in contracts]
        ib.sleep(wait)
        for sym, t in zip(chunk, tickers):
            d = getattr(t, "dividends", None)
            if d and d.nextDate:
                found[sym] = (str(d.nextDate), d.nextAmount)
        for c in contracts:
            try:
                ib.cancelMktData(c)
            except Exception:
                pass
        print(f"  {min(i + batch, len(symbols))}/{len(symbols)} scanned, "
              f"{len(found)} with a next ex-date", flush=True)
    return found


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--client-id", type=int, default=191)
    p.add_argument("--batch", type=int, default=25,
                   help="stocks subscribed at once (market-data lines)")
    p.add_argument("--wait", type=float, default=8.0,
                   help="seconds to wait for each batch's dividend ticks")
    args = p.parse_args()

    symbols = sorted(set(config.SP100_TICKERS))
    print(f"Fetching ex-dividend dates from IBKR for {len(symbols)} tickers", flush=True)

    ib = IB()
    try:
        _connect_with_retry(ib, args.client_id)
        ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)
        found = fetch(ib, symbols, args.batch, args.wait)
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    existing = _read_existing(OUT_PATH)
    before = len(existing)
    added = updated = 0
    for sym, (date, amt) in found.items():
        key = (sym, date)
        amt_s = "" if amt is None else f"{float(amt):.4f}".rstrip("0").rstrip(".")
        if key in existing:
            if amt_s and not existing[key]["Amount"]:
                existing[key]["Amount"] = amt_s
                updated += 1
        else:
            existing[key] = {"Symbol": sym, "ExDividendDate": date, "Amount": amt_s}
            added += 1

    rows = sorted(existing.values(), key=lambda r: (r["ExDividendDate"], r["Symbol"]))
    _atomic_write(OUT_PATH, rows)

    covered = len({r["Symbol"] for r in rows})
    print(f"\n{OUT_PATH}: {before} -> {len(rows)} rows "
          f"(+{added} new, {updated} amounts filled)")
    print(f"  distinct symbols covered: {covered}/{len(symbols)}")
    print(f"  IB reported a next ex-date for {len(found)}/{len(symbols)} tickers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
