"""Pull live S&P100 option chains from IBKR (delayed market data).

Pipeline per run:
  1. Connect to IB gateway.
  2. For each canonical SP100 ticker:
       a. Qualify underlying, fetch spot.
       b. Pull SecDefOptParams to discover available expirations + strikes.
       c. Pick expirations whose DTE falls in [DTE_MIN, DTE_MAX].
       d. Pick strikes within a generous delta-band proxy around spot
          (heuristic: ±15% around spot; the GROUND ranker filters precisely
          on AbsDelta downstream so we over-pull at this stage).
       e. Qualify the resulting Option contracts (puts AND calls).
  3. Batch through reqTickers (FETCH_BATCH_SIZE per call), collecting
     bid/ask/IV/Greeks.
  4. Assemble a DataFrame in the *backtest schema* expected by spreads.py:
       Symbol, DataDate, ExpirationDate, StrikePrice, PutCall,
       BidPrice, AskPrice, Theta, DTE, AbsDelta, UnderlyingPrice,
       ImpliedVolatility, OpenInterest
  5. Write parquet to live/snapshots/YYYY-MM-DD/HHMM.parquet.

CLI:
  python -m live.fetcher                  # full SP100
  python -m live.fetcher --tickers AAPL MSFT  # subset for smoke testing
  python -m live.fetcher --dry-run        # connect + qualify only, no market data
"""
from __future__ import annotations
import argparse
import os
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as backtest_config
from live import live_config

try:
    from ib_insync import IB, Stock, Option, util as ib_util
except ImportError as e:
    raise SystemExit("ib_insync not installed. `pip install ib_insync`") from e


warnings.filterwarnings("ignore", category=DeprecationWarning)


# ── Date helpers ────────────────────────────────────────────────────────────

def _weekly_expiries_in_dte_window(today: datetime.date) -> list[str]:
    """Return YYYYMMDD strings for Friday expiries whose DTE ∈ [DTE_MIN, DTE_MAX].

    The canonical config uses DTE ∈ [3, 8], which captures the upcoming
    Friday (and occasionally the following Friday if today is very early in
    the week). We propose candidates and let the chain lookup filter to
    what's actually listed.
    """
    expiries = []
    for offset in range(backtest_config.DTE_MIN, backtest_config.DTE_MAX + 1):
        d = today + timedelta(days=offset)
        if d.weekday() == 4:  # Friday
            expiries.append(d.strftime("%Y%m%d"))
    # Dedupe while preserving order
    seen = set()
    return [e for e in expiries if not (e in seen or seen.add(e))]


def _strike_window(spot: float) -> tuple[float, float]:
    """Strike band ±7% around spot (maps to 0.35–0.65 delta range).

    Tightened from ±15% to kill far OTM junk while capturing all
    delta-eligible strikes for canonical strategy.
    """
    return (0.93 * spot, 1.07 * spot)


# ── Tickers ─────────────────────────────────────────────────────────────────

def _tickers_from_arg(arg_list: list[str] | None) -> list[str]:
    if arg_list:
        return [t.upper() for t in arg_list]
    return list(backtest_config.SP100_TICKERS)


# ── Core fetch loop ─────────────────────────────────────────────────────────

def _qualify_options_for(
    ib: IB, symbol: str, expiry_strs: list[str], strike_lo: float, strike_hi: float
) -> list[Option]:
    """Build + qualify a list of Option contracts within the strike band."""
    # SecDefOptParams gives us listed strikes + expirations for the underlying.
    stock = Stock(symbol, "SMART", "USD")
    try:
        ib.qualifyContracts(stock)
    except Exception as e:
        print(f"  [{symbol}] qualifyContracts(stock) failed: {e}", flush=True)
        return []

    try:
        chains = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
    except Exception as e:
        print(f"  [{symbol}] reqSecDefOptParams failed: {e}", flush=True)
        return []

    smart = next((c for c in chains if c.exchange == "SMART"), None)
    if smart is None:
        return []

    available_exps = set(smart.expirations)
    target_exps = [e for e in expiry_strs if e in available_exps]
    if not target_exps:
        return []

    target_strikes = sorted(s for s in smart.strikes if strike_lo <= s <= strike_hi)
    if not target_strikes:
        return []

    raw_contracts: list[Option] = []
    for exp in target_exps:
        for k in target_strikes:
            for right in ("P", "C"):
                raw_contracts.append(Option(symbol, exp, k, right, "SMART"))

    try:
        qualified = ib.qualifyContracts(*raw_contracts)
    except Exception as e:
        print(f"  [{symbol}] qualifyContracts(options) failed: {e}", flush=True)
        return []

    return [c for c in qualified if c.conId]


def _batch_request_tickers(ib: IB, contracts: list[Option], batch_size: int):
    """Yield (contract, ticker) pairs in batches. Snapshot mode = no streaming."""
    for i in range(0, len(contracts), batch_size):
        batch = contracts[i:i + batch_size]
        tickers = ib.reqTickers(*batch)
        for c, t in zip(batch, tickers):
            yield c, t


def _row_from_ticker(c: Option, t, spot: float, today: datetime.date) -> dict | None:
    """Convert (contract, ticker) → a row in the backtest schema. Returns None if
    the row is unusable (no Greeks, no bid/ask, etc.)."""
    greeks = t.modelGreeks
    if greeks is None or greeks.delta is None:
        return None
    bid = t.bid if t.bid and t.bid > 0 else None
    ask = t.ask if t.ask and t.ask > 0 else None
    if bid is None or ask is None:
        return None

    expiry_date = datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d").date()
    dte = (expiry_date - today).days

    oi_val = t.callOpenInterest or t.putOpenInterest
    oi = int(oi_val) if pd.notna(oi_val) else 0

    return {
        "Symbol":            c.symbol,
        "DataDate":          pd.Timestamp(today),
        "ExpirationDate":    pd.Timestamp(expiry_date),
        "StrikePrice":       float(c.strike),
        "PutCall":           "put" if c.right == "P" else "call",
        "BidPrice":          float(bid),
        "AskPrice":          float(ask),
        "Theta":             float(greeks.theta) if greeks.theta is not None else 0.0,
        "DTE":               dte,
        "AbsDelta":          abs(float(greeks.delta)),
        "UnderlyingPrice":   float(spot),
        "ImpliedVolatility": float(greeks.impliedVol) if greeks.impliedVol is not None else 0.0,
        "OpenInterest":      oi,
    }


# ── Main entry point ────────────────────────────────────────────────────────

def fetch_snapshot(tickers: list[str], dry_run: bool = False, client_id: int = None) -> pd.DataFrame:
    if client_id is None:
        client_id = live_config.IB_CLIENT_ID

    today = datetime.now().date()
    expiry_strs = _weekly_expiries_in_dte_window(today)
    if not expiry_strs:
        print(f"  no Friday expiries in DTE window [{backtest_config.DTE_MIN}, "
              f"{backtest_config.DTE_MAX}] from {today}", flush=True)
        return pd.DataFrame()

    ib = IB()
    ib.connect(live_config.IB_HOST, live_config.IB_PORT,
               clientId=client_id)
    ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)

    rows: list[dict] = []
    t_start = time.monotonic()

    try:
        for sym in tickers:
            t_ticker_start = time.monotonic()
            # Spot first
            stock = Stock(sym, "SMART", "USD")
            try:
                ib.qualifyContracts(stock)
                [stock_ticker] = ib.reqTickers(stock)
                spot = stock_ticker.marketPrice()
                if pd.isna(spot) or spot <= 0:
                    spot = stock_ticker.close
            except Exception as e:
                print(f"  [{sym}] spot fetch failed: {e}", flush=True)
                continue
            if not spot or pd.isna(spot):
                print(f"  [{sym}] no spot price", flush=True)
                continue

            strike_lo, strike_hi = _strike_window(spot)
            contracts = _qualify_options_for(ib, sym, expiry_strs, strike_lo, strike_hi)
            if not contracts:
                print(f"  [{sym}] spot=${spot:.2f}  no qualified options", flush=True)
                continue

            if dry_run:
                print(f"  [{sym}] spot=${spot:.2f}  qualified={len(contracts)} (dry-run)", flush=True)
                continue

            ticker_rows = 0
            for c, t in _batch_request_tickers(ib, contracts, live_config.FETCH_BATCH_SIZE):
                row = _row_from_ticker(c, t, spot, today)
                if row:
                    rows.append(row)
                    ticker_rows += 1

            elapsed = time.monotonic() - t_ticker_start
            print(f"  [{sym}] spot=${spot:.2f}  rows={ticker_rows}/{len(contracts)} "
                  f"({elapsed:.1f}s)", flush=True)
    finally:
        ib.disconnect()

    total_elapsed = time.monotonic() - t_start
    print(f"\nfetched {len(rows)} option rows over {len(tickers)} tickers "
          f"in {total_elapsed:.1f}s", flush=True)
    return pd.DataFrame(rows)


def _write_snapshot(df: pd.DataFrame) -> Path:
    now = datetime.now()
    date_dir = Path(live_config.SNAPSHOTS_DIR) / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    out = date_dir / f"{now.strftime('%H%M')}.parquet"
    df.to_parquet(out, index=False)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*", default=None,
                        help="Override the SP100 ticker list (for testing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Connect + qualify only, skip market data")
    parser.add_argument("--client-id", type=int, default=None,
                        help="Override IB client ID (for parallel fetches)")
    args = parser.parse_args()

    tickers = _tickers_from_arg(args.tickers)
    print(f"Live fetch: {len(tickers)} tickers, DTE [{backtest_config.DTE_MIN}, "
          f"{backtest_config.DTE_MAX}]", flush=True)

    df = fetch_snapshot(tickers, dry_run=args.dry_run, client_id=args.client_id)
    if df.empty:
        print("no rows fetched; nothing to write", flush=True)
        return 1

    out = _write_snapshot(df)
    print(f"wrote {len(df)} rows → {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
