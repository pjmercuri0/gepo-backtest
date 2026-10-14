"""All 8-K item codes per SP100 ticker from SEC EDGAR (2026-10-14).

Companion to fetch_edgar_earnings_history.py, which keeps only Item 2.02. This keeps
EVERY item code so a "news gate" can be tested retroactively. Items that matter:
  1.03 bankruptcy          2.04 obligation acceleration   2.05 exit / disposal costs (LAYOFFS, restructuring)
  2.06 material impairment 4.02 non-reliance (restatement) 5.02 officer / director departure

CAVEAT, central to any conclusion drawn from this file: the column is the FILING date,
which an issuer may lodge up to four business days after the event, and press leaks can
precede the filing by much longer (Netflix's 2026-10-14 layoff was reported 2026-10-01).
A gate on this date is a measurement, not a live rule.

Usage: python3 research/fetch_edgar_8k_events.py [--start 2019-01-01] [--out output/edgar_8k_events.csv]
"""
from __future__ import annotations
import argparse, gzip, json, sys, time, urllib.request
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config

UA = {"User-Agent": "gepo-research research@cloudmallinc.com", "Accept-Encoding": "gzip"}
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


def events_for(cik: str) -> list[tuple[str, str]]:
    """(filingDate, items) for every 8-K, items being EDGAR's comma-separated codes."""
    out, p = [], get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    blocks = [p["filings"]["recent"]]
    for f in p["filings"].get("files", []):
        blocks.append(get(f"https://data.sec.gov/submissions/{f['name']}"))
        time.sleep(0.12)
    for b in blocks:
        items = b.get("items") or [""] * len(b["form"])
        for form, date, item in zip(b["form"], b["filingDate"], items):
            if form.startswith("8-K"):
                out.append((date, item or ""))
    return sorted(set(out))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--out", type=Path, default=Path("output/edgar_8k_events.csv"))
    a = ap.parse_args()
    if a.out.exists():
        sys.exit(f"{a.out} exists; refusing to overwrite -- pass a different --out")

    cm = cik_map()
    rows, failed, missing = [], [], [t for t in TICKERS if t not in cm]
    for i, t in enumerate(TICKERS, 1):
        if t not in cm:
            continue
        try:
            for d, items in events_for(cm[t]):
                if d >= a.start:
                    rows.append({"Symbol": t, "FilingDate": d, "Items": items})
        except Exception as exc:
            failed.append((t, type(exc).__name__))
        time.sleep(0.12)
        if i % 10 == 0 or i == len(TICKERS):
            print(f"  {i}/{len(TICKERS)} tickers, {len(rows):,} 8-Ks, {len(failed)} failures", flush=True)

    if missing:
        print(f"  no CIK for: {missing}")
    if failed:
        print(f"  FAILED: {failed}")
    out = pd.DataFrame(rows).drop_duplicates().sort_values(["FilingDate", "Symbol"])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.out, index=False)
    print(f"Wrote {a.out}: {len(out):,} 8-Ks for {out.Symbol.nunique()} symbols "
          f"({out.FilingDate.min()}..{out.FilingDate.max()})")


if __name__ == "__main__":
    main()
