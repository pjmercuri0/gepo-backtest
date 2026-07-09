"""Refresh recent daily closes for the snapshot settler.

Unlike fetch_yahoo_ohlc.py (one-time backfill that ends 2026-01-01 and SKIPS
existing files), this fetches a recent window and MERGES new dates into each
existing data/daily_bars_yahoo/{ticker}.csv. Existing rows are never
overwritten — new dates are appended and de-duplicated on `date`.

snapshot_picks.settle() reads these CSVs to find each pick's expiry close;
without a fresh feed, expired picks never settle (outcome/pnl stay null).
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

OUT = Path("data/daily_bars_yahoo")
ALIAS = {"SPXW": "^SPX", "RUTW": "^RUT"}
LOOKBACK_DAYS = 30
FINAL_BAR_HOUR_ET = 17


def _fetch(sym: str, p1: int, p2: int) -> pd.DataFrame | None:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
           f"?period1={p1}&period2={p2}&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.load(urllib.request.urlopen(req, timeout=30))
    r = data["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = pd.DataFrame({"ts": r["timestamp"], "open": q["open"], "high": q["high"],
                        "low": q["low"], "close": q["close"]})
    out["date"] = pd.to_datetime(out["ts"], unit="s", utc=True).dt.tz_convert(
        "America/New_York").dt.date.astype(str)
    out = out.dropna(subset=["high", "low", "close"])
    return out[["date", "open", "high", "low", "close"]]


def _incomplete_date_to_skip(now_utc: datetime) -> str | None:
    """Yahoo's current daily bar is intraday until after the close buffer."""
    now_et = pd.Timestamp(now_utc).tz_convert("America/New_York")
    if now_et.hour < FINAL_BAR_HOUR_ET:
        return str(now_et.date())
    return None


def main() -> None:
    now = datetime.now(timezone.utc)
    p1 = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp())
    p2 = int((now + timedelta(days=1)).timestamp())
    skip_date = _incomplete_date_to_skip(now)

    paths = sorted(OUT.glob("*.csv"))
    if not paths:
        print(f"[fetch_yahoo_recent] no existing CSVs in {OUT} — nothing to refresh")
        return

    added, unchanged, corrected, fail = 0, 0, 0, []
    for path in paths:
        ticker = path.stem
        sym = ALIAS.get(ticker, ticker)
        try:
            fresh = _fetch(sym, p1, p2)
        except Exception as e:  # network / parse errors — skip, never wipe
            fail.append(ticker)
            print(f"{ticker}: FAIL {e}", flush=True)
            time.sleep(0.7)
            continue
        if fresh is None or fresh.empty:
            unchanged += 1
            time.sleep(0.7)
            continue
        if skip_date:
            fresh = fresh[fresh["date"] != skip_date]
        existing = pd.read_csv(path, dtype={"date": str})
        dropped_existing = 0
        if skip_date and "date" in existing.columns:
            is_incomplete = existing["date"].astype(str).eq(skip_date)
            dropped_existing = int(is_incomplete.sum())
            if dropped_existing:
                existing = existing.loc[~is_incomplete].copy()
        before = set(existing["date"])
        merged = (pd.concat([existing, fresh], ignore_index=True)
                  .drop_duplicates(subset=["date"], keep="first")
                  .sort_values("date"))
        new_dates = sorted(set(merged["date"]) - before)
        if new_dates or dropped_existing:
            merged.to_csv(path, index=False)
            if new_dates:
                added += 1
                print(f"{ticker}: +{len(new_dates)} dates ({new_dates[0]}..{new_dates[-1]})", flush=True)
            if dropped_existing:
                corrected += 1
                print(f"{ticker}: dropped incomplete {skip_date} bar", flush=True)
        else:
            unchanged += 1
        time.sleep(0.7)

    print(f"\ndone: {added} updated, {unchanged} already current, "
          f"{corrected} corrected, failed: {fail}")


if __name__ == "__main__":
    main()
