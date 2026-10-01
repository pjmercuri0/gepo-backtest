"""Fetch historical stock-split events for the backtest universe from Yahoo.

Same endpoint and shape as fetch_yahoo_dividend_history.py, with events=split. Writes
output/yahoo_split_history.csv (Symbol, SplitDate, Numerator, Denominator, Ratio, Source).

Ratio > 1 is a forward split (10:1 -> 10.0, price falls); Ratio < 1 is a reverse split
(1:8 -> 0.125, price rises). Used by the entry-through-expiry corporate-action gate
(bear_regime_sweep.add_split_gate), the third exclusion beside earnings and ex-dividend.

    python3 research/fetch_yahoo_split_history.py
"""
from __future__ import annotations
import json, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import config

OUT = Path("output/yahoo_split_history.csv")
PERIOD1 = int(pd.Timestamp("2018-01-01", tz="UTC").timestamp())
PERIOD2 = int(pd.Timestamp("2027-01-01", tz="UTC").timestamp())
ALIASES = {"BRK.B": "BRK-B"}


def fetch(symbol: str) -> list[dict]:
    yahoo_symbol = ALIASES.get(symbol, symbol)
    payload = None; last_error = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = (f"https://{host}/v8/finance/chart/{urllib.parse.quote(yahoo_symbol)}"
               f"?period1={PERIOD1}&period2={PERIOD2}&interval=1d&events=split")
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
    events = (result.get("events") or {}).get("splits") or {}
    rows = []
    for event in events.values():
        ts = event.get("date")
        if ts is None:
            continue
        num = float(event.get("numerator") or 0); den = float(event.get("denominator") or 0)
        rows.append({"Symbol": symbol,
                     "SplitDate": pd.Timestamp(ts, unit="s", tz="UTC").tz_convert("America/New_York").date().isoformat(),
                     "Numerator": num, "Denominator": den,
                     "Ratio": (num / den) if den else None,
                     "Source": "Yahoo chart events"})
    return rows


def main() -> None:
    tickers = sorted(t for t in set(config.SP100_TICKERS) if t not in {"SPXW", "RUTW"})
    rows: list[dict] = []; failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        fut = {executor.submit(fetch, t): t for t in tickers}
        for i, f in enumerate(as_completed(fut), 1):
            t = fut[f]
            try:
                rows.extend(f.result())
            except Exception as exc:
                failures.append((t, f"{type(exc).__name__}: {exc}"))
            if i % 25 == 0 or i == len(tickers):
                print(f"{i}/{len(tickers)} tickers; events={len(rows):,}; failures={len(failures)}", flush=True)
            time.sleep(0.05)
    out = (pd.DataFrame(rows, columns=["Symbol", "SplitDate", "Numerator", "Denominator", "Ratio", "Source"])
           .drop_duplicates(["Symbol", "SplitDate"]).sort_values(["Symbol", "SplitDate"]).reset_index(drop=True))
    OUT.parent.mkdir(parents=True, exist_ok=True); out.to_csv(OUT, index=False)
    print(f"Wrote {OUT}: {len(out):,} splits for {out.Symbol.nunique()} symbols", flush=True)
    if failures:
        print("Failures:")
        for t, e in failures: print(f"  {t}: {e}")


if __name__ == "__main__":
    main()
