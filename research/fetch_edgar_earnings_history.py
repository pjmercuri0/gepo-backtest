"""Earnings-release dates from SEC EDGAR 8-K Item 2.02 filings (2026-10-01).

NASDAQ's historical calendar is incomplete: DE carried 3 dates for the whole 2020-2026
sample (its 135 backtest trades ran with no earnings gate), and where NASDAQ does have a
date it can disagree with the company's own filing (DE 2026-02-12 vs the 8-K on 02-19).
EDGAR is the primary source: every issuer files an 8-K tagged Item 2.02 ("Results of
Operations and Financial Condition") when it releases results.

Caveat: this is the FILING date. A company reporting after the close normally files the
same day, but a next-morning filing shifts the date by one session.

Usage: python3 research/fetch_edgar_earnings_history.py [--start 2019-01-01] [--out output/edgar_earnings_history.csv]
"""
from __future__ import annotations
import argparse, gzip, json, sys, time, urllib.request
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config

UA = {"User-Agent": "gepo-research research@cloudmallinc.com", "Accept-Encoding": "gzip"}   # www.sec.gov 403s any UA without a contact address
TICKERS = sorted(set(config.SP100_TICKERS) - {"RUTW", "SPXW", "SPY", "QQQ", "IWM"})


def get(url: str, retries: int = 4):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                raw = r.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode())
        except Exception:
            if attempt + 1 == retries:
                raise
            time.sleep(1.5 ** attempt)


def cik_map() -> dict[str, str]:
    d = get("https://www.sec.gov/files/company_tickers.json")
    return {v["ticker"]: f"{int(v['cik_str']):010d}" for v in d.values()}


def dates_for(cik: str) -> list[str]:
    out, p = [], get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    blocks = [p["filings"]["recent"]]
    for f in p["filings"].get("files", []):
        blocks.append(get(f"https://data.sec.gov/submissions/{f['name']}"))
        time.sleep(0.12)
    for b in blocks:
        items = b.get("items") or [""] * len(b["form"])
        for form, date, item in zip(b["form"], b["filingDate"], items):
            if form.startswith("8-K") and "2.02" in (item or ""):
                out.append(date)
    return sorted(set(out))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--out", type=Path, default=Path("output/edgar_earnings_history.csv"))
    a = ap.parse_args()

    cm = cik_map()
    missing = [t for t in TICKERS if t not in cm]
    rows, failed = [], []
    for i, t in enumerate(TICKERS, 1):
        if t not in cm:
            continue
        try:
            for d in dates_for(cm[t]):
                if d >= a.start:
                    rows.append({"Symbol": t, "EarningsDate": d})
        except Exception as exc:
            failed.append((t, type(exc).__name__))
        time.sleep(0.12)
        if i % 20 == 0 or i == len(TICKERS):
            print(f"  {i}/{len(TICKERS)} tickers, {len(rows):,} dates, {len(failed)} failures", flush=True)

    if missing:
        print(f"  no CIK for: {missing}")
    if failed:
        print(f"  FAILED: {failed}")
    out = pd.DataFrame(rows).drop_duplicates().sort_values(["EarningsDate", "Symbol"])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.out, index=False)
    print(f"Wrote {a.out}: {len(out):,} dates for {out.Symbol.nunique()} symbols "
          f"({out.EarningsDate.min()}..{out.EarningsDate.max()})")


if __name__ == "__main__":
    main()
