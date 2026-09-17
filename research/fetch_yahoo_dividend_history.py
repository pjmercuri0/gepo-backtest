"""Fetch historical ex-dividend events for the backtest universe from Yahoo.

Writes a research artifact only; it does not modify the live forward dividend
calendar.  The resulting file is used to enforce the bear-call assignment-risk
gate over 2020-2026.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config


OUT = Path("output/yahoo_dividend_history.csv")
PERIOD1 = int(pd.Timestamp("2019-01-01", tz="UTC").timestamp())
PERIOD2 = int(pd.Timestamp("2027-01-01", tz="UTC").timestamp())
ALIASES = {"BRK.B": "BRK-B"}


def fetch(symbol: str) -> list[dict]:
    yahoo_symbol = ALIASES.get(symbol, symbol)
    payload = None
    last_error = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = (
            f"https://{host}/v8/finance/chart/"
            f"{urllib.parse.quote(yahoo_symbol)}?period1={PERIOD1}&period2={PERIOD2}"
            "&interval=1d&events=div"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = json.load(response)
            break
        except Exception as exc:
            last_error = exc
    if payload is None:
        raise last_error  # type: ignore[misc]
    result = payload["chart"]["result"][0]
    events = (result.get("events") or {}).get("dividends") or {}
    rows = []
    for event in events.values():
        ts = event.get("date")
        if ts is None:
            continue
        rows.append(
            {
                "Symbol": symbol,
                "ExDividendDate": pd.Timestamp(ts, unit="s", tz="UTC")
                .tz_convert("America/New_York")
                .date()
                .isoformat(),
                "Amount": event.get("amount"),
                "Source": "Yahoo chart events",
            }
        )
    return rows


def main() -> None:
    tickers = sorted(
        t for t in set(config.SP100_TICKERS)
        if t not in {"SPXW", "RUTW"}
    )
    rows: list[dict] = []
    failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_ticker = {executor.submit(fetch, t): t for t in tickers}
        for i, future in enumerate(as_completed(future_to_ticker), 1):
            ticker = future_to_ticker[future]
            try:
                rows.extend(future.result())
            except Exception as exc:  # retain all failures in the final log
                failures.append((ticker, f"{type(exc).__name__}: {exc}"))
            if i % 20 == 0 or i == len(tickers):
                print(
                    f"{i}/{len(tickers)} tickers; events={len(rows):,}; "
                    f"failures={len(failures)}",
                    flush=True,
                )
            time.sleep(0.05)

    out = pd.DataFrame(
        rows,
        columns=["Symbol", "ExDividendDate", "Amount", "Source"],
    ).drop_duplicates(["Symbol", "ExDividendDate"])
    out = out.sort_values(["Symbol", "ExDividendDate"]).reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(
        f"Wrote {OUT}: {len(out):,} events for {out.Symbol.nunique()} symbols",
        flush=True,
    )
    if failures:
        print("Failures:")
        for ticker, error in failures:
            print(f"  {ticker}: {error}")


if __name__ == "__main__":
    main()
