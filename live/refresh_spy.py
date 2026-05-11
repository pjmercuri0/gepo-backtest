"""Refresh data/spy_us_d.csv from Yahoo Finance's chart API.

No API key, no third-party dependencies, no captcha. Uses the
public chart endpoint which returns JSON; we transform it into the
Stooq-style CSV that `spreads.build_regime_lookup` expects.

If the download or validation fails, the existing file is left untouched
and the regime filter just goes stale (the webapp already flags
`stale Xd` in the regime banner).

Schedule:
  Mya's server:  daily at 17:00 ET (after market close, after Yahoo's EOD update)
  P.J.'s Mac :   on-demand or daily cron — keeps local fetcher consistent

Run:
  python3 -m live.refresh_spy
  python3 -m live.refresh_spy --check     # validate freshness without re-downloading
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as backtest_config


# Yahoo's chart API. range=20y gives ~5000 daily bars (2006-present), which
# is plenty for the 100-day SMA regime filter. range=max auto-aggregates to
# weekly/monthly when crossing certain thresholds, so we avoid it.
URL = "https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=20y&interval=1d"
OUT_PATH  = Path(backtest_config.DATA_DIR) / "spy_us_d.csv"
TIMEOUT_S = 30
MIN_ROWS  = 1000   # sanity floor — should have thousands of days


def _download_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; gepo-live/1.0)",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read())


def _payload_to_csv(payload: dict) -> str:
    """Convert Yahoo chart payload to Stooq-style CSV.

    Crucially, the `Close` column we emit is the *adjusted* close (dividend-
    and split-adjusted). This means:
      - The regime filter still works (close vs SMA is invariant to which
        consistent series you pick).
      - SPY benchmark Sharpe computed downstream from pct_change(Close) is
        a TOTAL-RETURN Sharpe, matching what a SPY buy-and-hold investor
        actually experienced (dividends reinvested). Without this, the SPY
        Sharpe under-states reality by the dividend yield (~1.5%/yr).
    """
    res = payload.get("chart", {}).get("result")
    if not res:
        err = payload.get("chart", {}).get("error")
        raise ValueError(f"empty chart result; error={err}")
    r0 = res[0]
    timestamps = r0.get("timestamp") or []
    quote = (r0.get("indicators", {}).get("quote") or [{}])[0]
    adj_arr = (r0.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or []
    o = quote.get("open")  or []
    h = quote.get("high")  or []
    l = quote.get("low")   or []
    c = quote.get("close") or []
    v = quote.get("volume") or []

    n = min(len(timestamps), len(o), len(h), len(l), len(c), len(v))
    if not adj_arr:
        # Fall back to raw close with a stderr note.
        print("  ⚠ adjclose missing from payload; using raw close (no dividend adj)",
              flush=True)
        adj_arr = c
    if n == 0:
        raise ValueError("no candles in payload")

    out = ["Date,Open,High,Low,Close,Volume"]
    for i in range(n):
        if None in (o[i], h[i], l[i], c[i]):
            continue
        adj = adj_arr[i] if i < len(adj_arr) and adj_arr[i] is not None else c[i]
        d = datetime.fromtimestamp(timestamps[i], tz=timezone.utc).strftime("%Y-%m-%d")
        vol = v[i] if v[i] is not None else 0
        out.append(f"{d},{o[i]:.4f},{h[i]:.4f},{l[i]:.4f},{adj:.4f},{int(vol)}")
    return "\n".join(out) + "\n"


def _validate_csv(text: str) -> tuple[bool, str]:
    lines = text.strip().splitlines()
    if len(lines) < MIN_ROWS:
        return False, f"too few rows ({len(lines)} < {MIN_ROWS})"
    if not lines[0].lower().startswith("date"):
        return False, f"unexpected header: {lines[0]!r}"
    last = lines[-1].split(",")
    if len(last) < 5:
        return False, f"last row malformed: {lines[-1]!r}"
    return True, f"{len(lines)-1} rows, last date {last[0]}, last close ${float(last[4]):.2f}"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".csv")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _check_freshness() -> int:
    if not OUT_PATH.exists():
        print(f"  ✗ {OUT_PATH} does not exist")
        return 1
    mtime = datetime.fromtimestamp(OUT_PATH.stat().st_mtime)
    age_h = (datetime.now() - mtime).total_seconds() / 3600
    size  = OUT_PATH.stat().st_size
    print(f"  {OUT_PATH}")
    print(f"  size  : {size:,} bytes")
    print(f"  mtime : {mtime.isoformat(timespec='seconds')}  ({age_h:.1f}h ago)")
    with open(OUT_PATH, "rb") as f:
        try:
            f.seek(-200, os.SEEK_END)
        except OSError:
            f.seek(0)
        tail = f.read().decode("utf-8", errors="replace").splitlines()
    print(f"  last  : {tail[-1] if tail else '(empty)'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="Inspect the existing file without re-downloading")
    args = parser.parse_args()

    if args.check:
        return _check_freshness()

    print(f"refresh_spy: GET {URL}", flush=True)
    t0 = time.monotonic()
    try:
        payload = _download_json(URL)
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  ✗ download failed: {e}", flush=True)
        print(f"  existing {OUT_PATH} left untouched", flush=True)
        return 2

    try:
        csv_text = _payload_to_csv(payload)
    except ValueError as e:
        print(f"  ✗ payload transform failed: {e}", flush=True)
        print(f"  existing {OUT_PATH} left untouched", flush=True)
        return 3

    ok, msg = _validate_csv(csv_text)
    if not ok:
        print(f"  ✗ validation failed: {msg}", flush=True)
        print(f"  existing {OUT_PATH} left untouched", flush=True)
        return 4
    print(f"  ✓ validated: {msg}  ({time.monotonic()-t0:.2f}s)", flush=True)

    _atomic_write(OUT_PATH, csv_text)
    print(f"  ✓ wrote {OUT_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
