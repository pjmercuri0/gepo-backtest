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
import asyncio
import json
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
import spreads
from live import live_config
from live.regime import current_regime

# NYSE full-close holidays (reused from the backtest). When a weekly's Friday
# is a holiday (e.g. Juneteenth, Good Friday), the option expires the prior
# trading day — see _weekly_expiries_in_dte_window.
_NYSE_HOLIDAY_SET = set(spreads.NYSE_HOLIDAYS)

try:
    from ib_insync import IB, Stock, Option, util as ib_util
except ImportError as e:
    raise SystemExit("ib_insync not installed. `pip install ib_insync`") from e


warnings.filterwarnings("ignore", category=DeprecationWarning)


def _cache_day() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _cache_path(kind: str, symbol: str) -> Path:
    safe = symbol.replace("/", "_")
    return Path(live_config.CACHE_DIR) / _cache_day() / f"{kind}_{safe}.json"


def _read_json(path: Path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


def _contract_cache_key(expiry: str, strike: float, right: str) -> str:
    return f"{expiry}|{float(strike):.8g}|{right}"


def _contract_to_payload(c: Option) -> dict:
    return {
        "conId": int(c.conId),
        "symbol": c.symbol,
        "lastTradeDateOrContractMonth": c.lastTradeDateOrContractMonth,
        "strike": float(c.strike),
        "right": c.right,
        "exchange": c.exchange or "SMART",
        "currency": c.currency or "USD",
        "multiplier": c.multiplier,
        "localSymbol": c.localSymbol,
        "tradingClass": c.tradingClass,
    }


def _payload_to_contract(d: dict) -> Option:
    c = Option(
        d["symbol"],
        d["lastTradeDateOrContractMonth"],
        float(d["strike"]),
        d["right"],
        d.get("exchange") or "SMART",
    )
    c.conId = int(d["conId"])
    c.currency = d.get("currency") or "USD"
    c.multiplier = d.get("multiplier") or "100"
    c.localSymbol = d.get("localSymbol") or ""
    c.tradingClass = d.get("tradingClass") or ""
    return c


# ── Date helpers ────────────────────────────────────────────────────────────

def _weekly_expiries_in_dte_window(
    today: datetime.date,
    dte_min=None,
    dte_max=None,
) -> list[str]:
    """Return YYYYMMDD strings for weekly expiries in the active live DTE window.

    The canonical weekly expires Friday. When that Friday is an NYSE full-close
    holiday (e.g. Juneteenth, Good Friday), the contract instead expires the
    prior trading day (Thursday), so we roll the date back and re-check the DTE
    window. Without this the fetcher requests a Friday that isn't listed and
    every ticker comes back "no qualified options" (the 2026-06-19 Juneteenth
    case that produced zero live picks all week).
    """
    if dte_min is None or dte_max is None:
        dte_min, dte_max = live_config.live_dte_window(today)
    expiries: list[str] = []
    seen: set[str] = set()
    for offset in range(dte_min, dte_max + 1):
        d = today + timedelta(days=offset)
        if d.weekday() != 4:  # canonical weekly = Friday
            continue
        exp = d
        while exp.weekday() >= 5 or pd.Timestamp(exp) in _NYSE_HOLIDAY_SET:
            exp -= timedelta(days=1)
        dte = (exp - today).days
        if dte < dte_min or dte > dte_max:
            continue  # rolled out of window (e.g. Thursday entry → same-day expiry)
        s = exp.strftime("%Y%m%d")
        if s not in seen:
            seen.add(s)
            expiries.append(s)
    return expiries


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

async def _qualify_options_for(
    ib: IB, stock: Stock, expiry_strs: list[str], strike_lo: float, strike_hi: float,
    rights: tuple = ("P", "C"), spot: float | None = None,
) -> list[Option]:
    """Build + qualify a list of Option contracts within the strike band.

    `stock` must already be qualified by the caller (we reuse the spot-fetch
    Stock so SecDefOptParams can use its conId without a redundant qualify).

    `rights` filters which option types to request. Default fetches both
    puts and calls. In a known regime, callers pass ("P",) for bull regime
    (only bull_put spreads possible) or ("C",) for bear (only bear_call),
    halving the contract count.
    """
    symbol = stock.symbol
    chain_path = _cache_path("chain", symbol)
    chain_payload = _read_json(chain_path)
    if chain_payload:
        available_exps = set(chain_payload.get("expirations") or [])
        all_strikes = chain_payload.get("strikes") or []
    else:
        try:
            chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
        except Exception as e:
            print(f"  [{symbol}] reqSecDefOptParams failed: {e}", flush=True)
            return []

        smart = next((c for c in chains if c.exchange == "SMART"), None)
        if smart is None:
            return []
        available_exps = set(smart.expirations)
        all_strikes = sorted(float(s) for s in smart.strikes)
        _write_json(chain_path, {
            "symbol": symbol,
            "expirations": sorted(available_exps),
            "strikes": all_strikes,
        })

    target_exps = [e for e in expiry_strs if e in available_exps]
    if not target_exps:
        return []

    target_strikes = sorted(float(s) for s in all_strikes if strike_lo <= float(s) <= strike_hi)
    if not target_strikes:
        return []

    # Asymmetric per-right trim (2026-06-10): short legs are at most modestly
    # ITM (delta cap 0.65 ≈ ±4% of spot at weekly vols), so puts don't need
    # strikes far ABOVE spot and calls don't need strikes far BELOW. Cuts the
    # both-rights contract count ~25% without touching any eligible spread.
    mid_spot = spot if spot else (strike_lo + strike_hi) / 2.0
    put_hi  = 1.04 * mid_spot
    call_lo = 0.96 * mid_spot

    needed: list[tuple[str, float, str]] = []
    for exp in target_exps:
        for k in target_strikes:
            for right in rights:
                if right == "P" and k > put_hi:
                    continue
                if right == "C" and k < call_lo:
                    continue
                needed.append((exp, k, right))

    contract_path = _cache_path("contracts", symbol)
    cached_payload = _read_json(contract_path) or {}
    cached = cached_payload.get("contracts") or {}
    qualified_by_key: dict[str, Option] = {}
    missing: list[Option] = []
    for exp, k, right in needed:
        key = _contract_cache_key(exp, k, right)
        payload = cached.get(key)
        if payload and payload.get("conId"):
            qualified_by_key[key] = _payload_to_contract(payload)
        else:
            missing.append(Option(symbol, exp, k, right, "SMART"))

    if missing:
        try:
            qualified_missing = await ib.qualifyContractsAsync(*missing)
        except Exception as e:
            print(f"  [{symbol}] qualifyContracts(options) failed: {e}", flush=True)
            qualified_missing = []
        changed = False
        for c in qualified_missing:
            if not c.conId:
                continue
            key = _contract_cache_key(c.lastTradeDateOrContractMonth, c.strike, c.right)
            qualified_by_key[key] = c
            cached[key] = _contract_to_payload(c)
            changed = True
        if changed:
            _write_json(contract_path, {"symbol": symbol, "contracts": cached})

    return [qualified_by_key[_contract_cache_key(exp, k, right)]
            for exp, k, right in needed
            if _contract_cache_key(exp, k, right) in qualified_by_key]


async def _fetch_one_ticker(
    ib: IB, sym: str, expiry_strs: list[str], rights: tuple,
    today: datetime.date, batch_size: int, dry_run: bool,
) -> tuple[list[dict], str]:
    """Fetch one ticker's spot + qualified options + Greeks.

    Returns (rows, log_line). All IB calls are awaited so multiple tickers
    can run concurrently on a single IB connection.
    """
    t_start = time.monotonic()
    stock = Stock(sym, "SMART", "USD")
    try:
        await ib.qualifyContractsAsync(stock)
        [stock_ticker] = await ib.reqTickersAsync(stock)
        spot = stock_ticker.marketPrice()
        if pd.isna(spot) or spot <= 0:
            spot = stock_ticker.close
    except Exception as e:
        return [], f"  [{sym}] spot fetch failed: {e}"
    if not spot or pd.isna(spot):
        return [], f"  [{sym}] no spot price"

    strike_lo, strike_hi = _strike_window(spot)
    contracts = await _qualify_options_for(ib, stock, expiry_strs, strike_lo, strike_hi, rights=rights, spot=spot)
    if not contracts:
        return [], f"  [{sym}] spot=${spot:.2f}  no qualified options"

    if dry_run:
        return [], f"  [{sym}] spot=${spot:.2f}  qualified={len(contracts)} (dry-run)"

    rows: list[dict] = []
    try:
        for i in range(0, len(contracts), batch_size):
            batch = contracts[i:i + batch_size]
            tickers = await ib.reqTickersAsync(*batch)
            for c, t in zip(batch, tickers):
                row = _row_from_ticker(c, t, spot, today)
                if row:
                    rows.append(row)
    except Exception as e:
        elapsed = time.monotonic() - t_start
        return rows, f"  [{sym}] reqTickers failed mid-fetch: {e} ({elapsed:.1f}s, {len(rows)} rows kept)"

    elapsed = time.monotonic() - t_start
    return rows, f"  [{sym}] spot=${spot:.2f}  rows={len(rows)}/{len(contracts)} ({elapsed:.1f}s)"


async def _fetch_all_tickers(
    ib: IB, tickers: list[str], expiry_strs: list[str], rights: tuple,
    today: datetime.date, batch_size: int, dry_run: bool,
) -> list[dict]:
    """Run per-ticker fetches concurrently on the single IB connection.

    asyncio.gather lets one fetcher process its full ticker list in roughly
    the time of its slowest single ticker, instead of sum-of-all.
    """
    results = await asyncio.gather(
        *[_fetch_one_ticker(ib, sym, expiry_strs, rights, today, batch_size, dry_run)
          for sym in tickers],
        return_exceptions=True,
    )
    all_rows: list[dict] = []
    for sym, r in zip(tickers, results):
        if isinstance(r, Exception):
            print(f"  [{sym}] exception during fetch: {r}", flush=True)
            continue
        rows, log = r
        print(log, flush=True)
        all_rows.extend(rows)
    return all_rows


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
    # LastPrice — most recent trade for this option. Used downstream as the
    # canonical credit basis for fill projection (matches backtest). Can be
    # stale (last trade from hours ago) or 0 (untraded); webapp falls back
    # to 0.80 × mid if last is missing/zero.
    last = t.last if t.last and t.last > 0 else None

    expiry_date = datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d").date()
    dte = (expiry_date - today).days

    oi_val = t.callOpenInterest or t.putOpenInterest
    oi = int(oi_val) if pd.notna(oi_val) else 0
    # Volume — today's traded contracts. Updates in real time, unlike OI
    # which IBKR doesn't populate intraday. Used for live liquidity gating
    # (Volume ≥ 100 is our backtest-equivalent threshold for OI ≥ 100).
    vol_val = t.volume
    volume = int(vol_val) if vol_val is not None and pd.notna(vol_val) and vol_val >= 0 else 0

    return {
        "Symbol":            c.symbol,
        "DataDate":          pd.Timestamp(today),
        "ExpirationDate":    pd.Timestamp(expiry_date),
        "StrikePrice":       float(c.strike),
        "PutCall":           "put" if c.right == "P" else "call",
        "BidPrice":          float(bid),
        "AskPrice":          float(ask),
        "LastPrice":         float(last) if last is not None else 0.0,
        "Theta":             float(greeks.theta) if greeks.theta is not None else 0.0,
        "DTE":               dte,
        "AbsDelta":          abs(float(greeks.delta)),
        "UnderlyingPrice":   float(spot),
        "ImpliedVolatility": float(greeks.impliedVol) if greeks.impliedVol is not None else 0.0,
        "OpenInterest":      oi,
        "Volume":            volume,
        # Top-of-book size (contracts resting at the BBO). A 1-lot quote on a
        # wide market is exactly the phantom-liquidity case that inflates b;
        # captured for live liquidity diagnostics/gating.
        "BidSize":           int(t.bidSize) if t.bidSize is not None and pd.notna(t.bidSize) and t.bidSize >= 0 else 0,
        "AskSize":           int(t.askSize) if t.askSize is not None and pd.notna(t.askSize) and t.askSize >= 0 else 0,
    }


# ── Main entry point ────────────────────────────────────────────────────────

def fetch_snapshot(tickers: list[str], dry_run: bool = False, client_id: int = None) -> pd.DataFrame:
    if client_id is None:
        client_id = live_config.IB_CLIENT_ID

    today = datetime.now().date()
    dte_min, dte_max = live_config.live_dte_window(today)
    expiry_strs = _weekly_expiries_in_dte_window(today, dte_min, dte_max)
    if not expiry_strs:
        print(f"  no Friday expiries in DTE window [{dte_min}, {dte_max}] "
              f"from {today}", flush=True)
        return pd.DataFrame()

    # Both rights, always. The regime gate is OFF in canon (2026-06-05:
    # gating cost ~27% of profit; backtest builds both directions every day).
    # The old regime-aware right filter (puts-only in bull) survived here
    # until 2026-06-10 and silently suppressed every bear_call live.
    regime = current_regime()
    rights = ("P", "C")
    print(f"  regime={regime.get('regime')} (gate OFF) → fetching rights={rights}", flush=True)

    ib = None
    for attempt in range(1, live_config.IB_CONNECT_ATTEMPTS + 1):
        ib = IB()
        try:
            ib.connect(
                live_config.IB_HOST,
                live_config.IB_PORT,
                clientId=client_id,
                timeout=live_config.IB_CONNECT_TIMEOUT,
            )
            break
        except Exception as error:
            ib.disconnect()
            if attempt == live_config.IB_CONNECT_ATTEMPTS:
                raise RuntimeError(
                    f"IB Gateway connection failed after {attempt} attempts "
                    f"(clientId={client_id})"
                ) from error
            delay = live_config.IB_CONNECT_RETRY_DELAY * attempt
            print(
                f"  IB connection attempt {attempt}/{live_config.IB_CONNECT_ATTEMPTS} "
                f"failed ({error.__class__.__name__}); retrying in {delay}s",
                flush=True,
            )
            time.sleep(delay)

    assert ib is not None
    ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)

    t_start = time.monotonic()
    try:
        rows = ib.run(_fetch_all_tickers(
            ib, tickers, expiry_strs, rights, today,
            live_config.FETCH_BATCH_SIZE, dry_run,
        ))
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
    parser.add_argument("--out", type=str, default=None,
                        help="Override output parquet path (for parallel runs that "
                             "would otherwise collide on HHMM.parquet)")
    args = parser.parse_args()

    tickers = _tickers_from_arg(args.tickers)
    dte_min, dte_max = live_config.live_dte_window(datetime.now().date())
    print(f"Live fetch: {len(tickers)} tickers, DTE [{dte_min}, {dte_max}]",
          flush=True)

    df = fetch_snapshot(tickers, dry_run=args.dry_run, client_id=args.client_id)
    if df.empty:
        print("no rows fetched; nothing to write", flush=True)
        return 1

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
    else:
        out = _write_snapshot(df)
    print(f"wrote {len(df)} rows → {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
