"""Refresh data/spy_us_d.csv from IBKR daily bars (replaces Stooq dependency).

Pulls the longest practical daily history from IB Gateway via
`reqHistoricalData` and writes the canonical CSV format:
    Date,Open,High,Low,Close,Volume

Run after the original Stooq file has gone stale, or whenever you want
a deeper history. Uses a separate clientId so it doesn't collide with
the regular SPY tick fetcher.
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as backtest_config
from live import live_config

try:
    from ib_insync import IB, Stock
except ImportError as e:
    raise SystemExit("ib_insync not installed. `pip install ib_insync`") from e


OUT_PATH = Path(backtest_config.DATA_DIR) / "spy_us_d.csv"


def fetch(duration: str = "20 Y") -> pd.DataFrame:
    ib = IB()
    # Reuse a unique clientId so this doesn't collide with the regular SPY
    # ticker (IB_CLIENT_ID + 1) or option fetchers (100-109).
    ib.connect(live_config.IB_HOST, live_config.IB_PORT,
               clientId=live_config.IB_CLIENT_ID + 10)
    ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)
    try:
        stock = Stock("SPY", "SMART", "USD")
        ib.qualifyContracts(stock)
        bars = ib.reqHistoricalData(
            stock, endDateTime="",
            durationStr=duration,
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
        )
    finally:
        ib.disconnect()

    if not bars:
        raise SystemExit("IB returned no bars")
    rows = []
    for b in bars:
        d = b.date if hasattr(b.date, "isoformat") else b.date
        rows.append({
            "Date":   d.isoformat() if hasattr(d, "isoformat") else str(d),
            "Open":   round(float(b.open), 4),
            "High":   round(float(b.high), 4),
            "Low":    round(float(b.low), 4),
            "Close":  round(float(b.close), 4),
            "Volume": int(b.volume) if b.volume is not None else 0,
        })
    df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
    return df


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", default="20 Y",
                        help='IB durationStr (e.g. "20 Y", "10 Y", "5 Y"). Default 20 Y.')
    parser.add_argument("--out", default=str(OUT_PATH),
                        help="Output CSV path. Default replaces data/spy_us_d.csv.")
    args = parser.parse_args()

    print(f"fetching SPY daily bars ({args.duration}) from IBKR...", flush=True)
    df = fetch(args.duration)
    print(f"  got {len(df)} bars: {df['Date'].iloc[0]} → {df['Date'].iloc[-1]}", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"wrote {out}  ({out.stat().st_size/1e3:.0f} KB)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
