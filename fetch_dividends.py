"""
Scrape NASDAQ's public dividend calendar for SP100 tickers, save to
data/dividend_calendar.csv. Mirrors fetch_earnings.py — same NASDAQ API
endpoint family, just calendar/dividends instead of calendar/earnings.

We capture the EX-DIVIDEND date (the date short-call holders may early-
exercise to capture the dividend; the trigger for assignment risk).

Usage:  python3 fetch_dividends.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
                                   [--workers N]

Writes a SEPARATE NEW file (no parquet/CSV wipe risk). If the output file
already exists, this script MERGES new rows with existing ones, preserving
history.
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pandas as pd
import requests

import config

OUT_PATH = os.path.join(config.DATA_DIR, "dividend_calendar.csv")
TICKERS  = set(config.SP100_TICKERS)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_one_day(date_str, session, retries=3):
    """One-day NASDAQ dividend calendar fetch.

    Endpoint returns rows with fields: symbol, dividend_Ex_Date,
    payment_Date, record_Date, dividend_Rate. We capture Symbol +
    EX-dividend date (the trigger for short-call assignment risk).
    """
    url = f"https://api.nasdaq.com/api/calendar/dividends?date={date_str}"
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                rows = (data or {}).get("data", {}).get("calendar", {}).get("rows") or []
                return [
                    {"Symbol": row.get("symbol"), "ExDividendDate": date_str}
                    for row in rows if row.get("symbol") in TICKERS
                ]
            elif r.status_code == 429:
                time.sleep(2.0 ** attempt)
                continue
            else:
                return []
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return []


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start",   default=None, help="default: today")
    p.add_argument("--end",     default=None, help="default: today + 120 days")
    p.add_argument("--workers", type=int, default=10)
    args = p.parse_args()

    today = pd.Timestamp.today().normalize()
    start = pd.Timestamp(args.start) if args.start else today
    end   = pd.Timestamp(args.end)   if args.end   else today + pd.Timedelta(days=120)

    weekdays = pd.bdate_range(start, end)
    n_total  = len(weekdays)
    print(f"Fetching dividend ex-dates for {n_total:,} weekdays from "
          f"{start.date()} to {end.date()}")
    print(f"Filtering to {len(TICKERS)} SP100 tickers, "
          f"{args.workers} concurrent workers")

    session = requests.Session()
    session.headers.update(HEADERS)
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=args.workers, pool_maxsize=args.workers
    )
    session.mount("https://", adapter)

    collected = []
    done_count = 0
    lock = Lock()
    t0 = time.time()

    def worker(date_str):
        return fetch_one_day(date_str, session)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(worker, d.strftime("%Y-%m-%d")): d for d in weekdays}
        for fut in as_completed(futures):
            rows = fut.result()
            with lock:
                collected.extend(rows)
                done_count += 1
                if done_count % 25 == 0 or done_count == n_total:
                    rate = done_count / max(time.time() - t0, 0.01)
                    eta  = (n_total - done_count) / max(rate, 0.01)
                    print(f"  {done_count}/{n_total} ({rate:.1f}/s, ETA {eta:.0f}s)  "
                          f"collected: {len(collected)}",
                          flush=True)

    new_df = pd.DataFrame(collected).drop_duplicates()

    # MERGE with existing file rather than replace — preserves history per
    # the "never wipe data without permission" rule.
    if os.path.exists(OUT_PATH):
        old = pd.read_csv(OUT_PATH)
        merged = pd.concat([old, new_df], ignore_index=True).drop_duplicates()
        merged = merged.sort_values(['Symbol', 'ExDividendDate']).reset_index(drop=True)
        merged.to_csv(OUT_PATH, index=False)
        added = len(merged) - len(old)
        print(f"\nMerged into {OUT_PATH}: {len(merged):,} total rows "
              f"(+{added} new), {merged['Symbol'].nunique()} tickers")
    else:
        new_df = new_df.sort_values(['Symbol', 'ExDividendDate']).reset_index(drop=True)
        new_df.to_csv(OUT_PATH, index=False)
        print(f"\nWrote {OUT_PATH}: {len(new_df):,} rows, "
              f"{new_df['Symbol'].nunique()} tickers")


if __name__ == "__main__":
    main()
