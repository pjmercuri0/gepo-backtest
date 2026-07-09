"""Fetch last 20 daily TRADES bars per SP100 ticker from IBKR, compute RV(10d),
merge into output/rv_table.parquet.

Why this script exists: vendor data is delivered ~1 day late and we'd previously
forward-fill stale vendor RV onto today's picks. With IBKR daily bars we get
today's close (after market close at 16:00 ET) so RV is as fresh as possible,
fully self-sufficient — no vendor dependency for live RV.

Cron: 31 16 * * 1-5 /Users/mercurio/Downloads/gepo-backtest/live/cron_daily_bars.sh
Runs at 16:31 (31 min after 16:00 close) so today's bar is finalized. Next
morning's cron_parallel reads the fresh rv_table.
"""
from __future__ import annotations
import os, sys, time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as bt_config
from live import live_config

try:
    from ib_insync import IB, Stock
except ImportError as e:
    raise SystemExit("ib_insync not installed. `pip install ib_insync`") from e


WINDOW_DAYS = 10           # 10-day RV — matches rv_table.py canonical
MIN_OBS     = 5            # require ≥5 returns to report
LOOKBACK    = "20 D"       # pull 20 daily bars to have buffer for the 10-day window
OUT_PATH    = ROOT / "output" / "rv_table.parquet"


def _bars_to_rv(bars, ticker: str) -> pd.DataFrame | None:
    """Given list of ib_insync BarData, compute rolling 10d RV per close."""
    if not bars or len(bars) < MIN_OBS + 1:
        return None
    df = pd.DataFrame([
        {"DataDate": pd.Timestamp(b.date), "close": float(b.close)}
        for b in bars
    ]).sort_values("DataDate").reset_index(drop=True)
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
    df["rv_30d"]  = (df["log_ret"].rolling(WINDOW_DAYS, min_periods=MIN_OBS).std()
                     * np.sqrt(252))
    df["Symbol"] = ticker
    return df.dropna(subset=["rv_30d"])[["Symbol", "DataDate", "rv_30d"]]


def fetch_all(tickers: list[str]) -> pd.DataFrame:
    ib = IB()
    ib.connect(live_config.IB_HOST, live_config.IB_PORT,
               clientId=live_config.IB_CLIENT_ID + 2)
    ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)
    rows = []
    try:
        for tk in tickers:
            try:
                stock = Stock(tk, "SMART", "USD")
                ib.qualifyContracts(stock)
                bars = ib.reqHistoricalData(
                    stock, endDateTime="",
                    durationStr=LOOKBACK,
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                )
                rv = _bars_to_rv(bars, tk)
                if rv is None or rv.empty:
                    print(f"  {tk:<6} insufficient bars (n={len(bars) if bars else 0})", flush=True)
                    continue
                rows.append(rv)
                latest = rv.iloc[-1]
                print(f"  {tk:<6} {len(bars)} bars, RV@{latest['DataDate'].date()} = "
                      f"{latest['rv_30d']*100:5.1f}%", flush=True)
            except Exception as e:
                print(f"  {tk:<6} ERR: {type(e).__name__}: {e}", flush=True)
                continue
            time.sleep(0.05)  # gentle pacing under IBKR rate limits
    finally:
        ib.disconnect()
    if not rows:
        return pd.DataFrame(columns=["Symbol", "DataDate", "rv_30d"])
    return pd.concat(rows, ignore_index=True)


def merge_into_table(new_rv: pd.DataFrame) -> None:
    """Merge new RV rows into existing rv_table.parquet, overwriting same-key rows."""
    if new_rv.empty:
        print("No new rows; skipping write.", flush=True)
        return
    new_rv["DataDate"] = pd.to_datetime(new_rv["DataDate"])
    if OUT_PATH.exists():
        old = pd.read_parquet(OUT_PATH)
        old["DataDate"] = pd.to_datetime(old["DataDate"])
        # Drop rows where (Symbol, DataDate) exists in new
        old = old.merge(new_rv[["Symbol", "DataDate"]].assign(_new=1),
                        on=["Symbol", "DataDate"], how="left")
        old = old[old["_new"].isna()].drop(columns=["_new"])
        merged = pd.concat([old, new_rv], ignore_index=True)
    else:
        merged = new_rv
    merged = merged.sort_values(["Symbol", "DataDate"]).reset_index(drop=True)
    merged.to_parquet(OUT_PATH, index=False)
    print(f"\nWrote {OUT_PATH}: {len(merged):,} rows ({merged['Symbol'].nunique()} tickers, "
          f"latest {merged['DataDate'].max().date()})", flush=True)


def main() -> int:
    tickers = sorted(set(bt_config.SP100_TICKERS))
    print(f"=== {datetime.now().isoformat(timespec='seconds')} ===")
    print(f"Fetching {LOOKBACK} daily TRADES bars for {len(tickers)} SP100 tickers...")
    new_rv = fetch_all(tickers)
    print(f"\nCollected {len(new_rv):,} rows across {new_rv['Symbol'].nunique() if not new_rv.empty else 0} tickers.")
    merge_into_table(new_rv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
