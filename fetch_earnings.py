"""
Scrape NASDAQ's public earnings calendar for SP100 tickers across the
backtest range, save to data/earnings_calendar.csv. Uses a thread pool
for concurrency so NASDAQ's per-request latency doesn't dominate.

Usage:  python3 fetch_earnings.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
                                  [--workers N]
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

OUT_PATH = os.path.join(config.DATA_DIR, "earnings_calendar.csv")
TICKERS  = set(config.SP100_TICKERS)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_one_day(date_str, session, retries=3):
    url = f"https://api.nasdaq.com/api/calendar/earnings?date={date_str}"
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                rows = (data or {}).get("data", {}).get("rows") or []
                return [
                    {"Symbol": row.get("symbol"), "EarningsDate": date_str}
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
    p.add_argument("--start",   default="2020-01-01")
    p.add_argument("--end",     default="2026-05-04")
    p.add_argument("--workers", type=int, default=10)
    args = p.parse_args()

    weekdays = pd.bdate_range(args.start, args.end)
    n_total  = len(weekdays)
    print(f"Fetching {n_total:,} weekdays from {args.start} to {args.end}")
    print(f"Filtering to {len(TICKERS)} SP100 tickers, "
          f"{args.workers} concurrent workers")

    session = requests.Session()
    session.headers.update(HEADERS)
    # Mount a pool large enough for our worker count
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
                if done_count % 50 == 0 or done_count == n_total:
                    elapsed = time.time() - t0
                    rate    = done_count / elapsed if elapsed > 0 else 0
                    eta     = (n_total - done_count) / rate if rate > 0 else 0
                    print(f"  {done_count:,}/{n_total:,} "
                          f"({rate:.1f}/s, ETA {eta:.0f}s)  "
                          f"collected: {len(collected):,}",
                          flush=True)

    if not collected:
        print("ERROR: no rows collected.")
        sys.exit(1)

    new_df = pd.DataFrame(collected).drop_duplicates()
    new_df["EarningsDate"] = pd.to_datetime(new_df["EarningsDate"])

    os.makedirs(config.DATA_DIR, exist_ok=True)

    # MERGE with existing file rather than REPLACE — preserves historical
    # entries across refresh runs. Hard rule 2026-06-08: never wipe data files.
    if os.path.exists(OUT_PATH):
        old = pd.read_csv(OUT_PATH)
        old["EarningsDate"] = pd.to_datetime(old["EarningsDate"])
        merged = (pd.concat([old, new_df], ignore_index=True)
                    .drop_duplicates(subset=["Symbol", "EarningsDate"]))
        added = len(merged) - len(old)
        merged = merged.sort_values(["EarningsDate", "Symbol"]).reset_index(drop=True)
        merged.to_csv(OUT_PATH, index=False)
        print(f"\nMerged into {OUT_PATH}: {len(merged):,} total rows "
              f"(+{added} new), range {merged['EarningsDate'].min().date()} → "
              f"{merged['EarningsDate'].max().date()}")
    else:
        df = new_df.sort_values(["EarningsDate", "Symbol"]).reset_index(drop=True)
        df.to_csv(OUT_PATH, index=False)
        print(f"\nWrote {OUT_PATH}: {len(df):,} rows")
    print(f"\nSaved: {OUT_PATH}  ({len(df):,} (Symbol, EarningsDate) pairs)")
    print(f"Tickers covered: {df['Symbol'].nunique()}/{len(TICKERS)}")
    print(f"Range: {df['EarningsDate'].min().date()} → "
          f"{df['EarningsDate'].max().date()}")


if __name__ == "__main__":
    main()
