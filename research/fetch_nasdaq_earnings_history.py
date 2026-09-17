"""Fetch a research-only historical NASDAQ earnings calendar.

The live calendar is intentionally left untouched.  This artifact is used to
apply the live ranker's entry-through-expiry earnings exclusion to historical
bear-call research.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config


OUT = Path("output/nasdaq_earnings_history.csv")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
TICKERS = set(config.SP100_TICKERS)


def fetch_day(date: pd.Timestamp, retries: int = 4) -> tuple[str, list[dict], str | None]:
    date_str = date.strftime("%Y-%m-%d")
    url = f"https://api.nasdaq.com/api/calendar/earnings?date={date_str}"
    for attempt in range(retries):
        try:
            request = Request(url, headers=HEADERS)
            with urlopen(request, timeout=20) as response:
                status = response.status
                payload = json.loads(response.read().decode("utf-8"))
            if status == 200:
                rows = ((payload or {}).get("data") or {}).get("rows") or []
                kept = [
                    {"Symbol": row.get("symbol"), "EarningsDate": date_str}
                    for row in rows
                    if row.get("symbol") in TICKERS
                ]
                return date_str, kept, None
        except HTTPError as exc:
            if exc.code != 429 and exc.code < 500:
                return date_str, [], f"HTTP {exc.code}"
            if attempt + 1 == retries:
                return date_str, [], f"HTTP {exc.code}"
            time.sleep(1.5**attempt)
        except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            if attempt + 1 == retries:
                return date_str, [], type(exc).__name__
            time.sleep(1.5**attempt)
    return date_str, [], "retry exhaustion"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2026-08-31")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()

    dates = list(pd.bdate_range(args.start, args.end))
    collected: list[dict] = []
    failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(fetch_day, date) for date in dates]
        for done, future in enumerate(as_completed(futures), 1):
            date_str, rows, error = future.result()
            collected.extend(rows)
            if error:
                failures.append((date_str, error))
            if done % 250 == 0 or done == len(dates):
                print(
                    f"{done:,}/{len(dates):,} days; {len(collected):,} events; "
                    f"{len(failures):,} failures",
                    flush=True,
                )

    if failures:
        sample = ", ".join(f"{date}: {error}" for date, error in failures[:10])
        raise RuntimeError(
            f"calendar incomplete: {len(failures)} failed dates; first failures: {sample}"
        )
    if not collected:
        raise RuntimeError("NASDAQ returned no in-universe earnings events")

    result = (
        pd.DataFrame(collected)
        .drop_duplicates(["Symbol", "EarningsDate"])
        .sort_values(["EarningsDate", "Symbol"])
        .reset_index(drop=True)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)
    print(
        f"Wrote {args.output}: {len(result):,} events for "
        f"{result.Symbol.nunique():,} symbols"
    )


if __name__ == "__main__":
    main()
