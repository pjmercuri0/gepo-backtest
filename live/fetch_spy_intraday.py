"""Pull a single SPY intraday snapshot from IBKR.

Smoke test for the IBKR connection that doesn't require OPRA / options
market data. SPY equity quotes work on every IBKR account (delayed mode
ships free). The script:

  1. Connects to the IB Gateway on port 4002 (paper).
  2. Qualifies SPY as a SMART/USD stock.
  3. Requests one snapshot ticker (bid/ask/last/close).
  4. Pulls the most recent 100d SMA from data/spy_us_d.csv for regime context.
  5. Writes everything to live/snapshots/spy_intraday.json (atomic).

The webapp picks up the file via /api/spy/latest.json and displays a live
SPY widget under the regime banner.

CLI:
  python3 -m live.fetch_spy_intraday              # one snapshot, exit
  python3 -m live.fetch_spy_intraday --print      # also pretty-print to stdout

Cron (Mon-Fri during market hours, every 15 min):
  */15 9-16 * * 1-5  cd <repo> && python3 -m live.fetch_spy_intraday \\
      >> live/logs/spy_intraday.log 2>&1
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import tempfile
import time
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


# Writes into ranked/ (not snapshots/) so the same rsync that ships
# ranked/latest.json to Mya also picks up the SPY intraday tick.
OUT_PATH = Path(live_config.RANKED_DIR) / "spy_intraday.json"


def _sma_context_from_ib(ib, stock) -> dict:
    """Pull last 150 daily SPY bars from IBKR and compute the 100d SMA.

    Replaces the prior Stooq CSV dependency so the regime is always current
    without needing manual file refreshes. 150d gives us a buffer above the
    100-day window in case of holidays/half-days.
    """
    window = int(backtest_config.REGIME_WINDOW)
    try:
        bars = ib.reqHistoricalData(
            stock, endDateTime="",
            durationStr=f"{window + 50} D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
        )
    except Exception as e:
        print(f"  ✗ SPY historical fetch failed: {e}", flush=True)
        return {}
    if not bars or len(bars) < window:
        print(f"  ✗ SPY historical returned {len(bars) if bars else 0} bars (need ≥{window})", flush=True)
        return {}
    closes = [float(b.close) for b in bars[-window:]]
    sma = sum(closes) / len(closes)
    # 20-day realized vol (annualized %, from log returns). Used by the
    # vol-gate in ranker.py: high RV → skip non-Monday entries.
    rv_window = 20
    rv_20 = None
    if len(bars) >= rv_window + 1:
        recent = [float(b.close) for b in bars[-(rv_window + 1):]]
        rets = [(recent[i] / recent[i-1] - 1) for i in range(1, len(recent))]
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
        rv_20 = round((var_r ** 0.5) * (252 ** 0.5) * 100, 2)
    last = bars[-1]
    return {
        "sma_as_of": last.date.isoformat() if hasattr(last.date, "isoformat") else str(last.date),
        "sma_close": round(float(last.close), 2),
        "sma_100":   round(sma, 2),
        "sma_window": window,
        "rv_20":     rv_20,
    }


def fetch(ib: "IB | None" = None) -> dict:
    # serve() hands in its held connection; a one-shot logs in here. A fresh login
    # during the eight daemons' snapshot burst timed out (15s) on both attempts on
    # 2026-11-09 12:30, 12:45, 13:30, 14:00 and 14:27 -- hence the daemon.
    own = ib is None
    if own:
        ib = IB()
        ib.connect(live_config.IB_HOST, live_config.IB_PORT,
                   clientId=live_config.IB_CLIENT_ID + 1, readonly=True,
                   timeout=live_config.IB_CONNECT_TIMEOUT)
    ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)

    try:
        stock = Stock("SPY", "SMART", "USD")
        ib.qualifyContracts(stock)
        [t] = ib.reqTickers(stock)

        bid = float(t.bid) if t.bid and t.bid > 0 else None
        ask = float(t.ask) if t.ask and t.ask > 0 else None
        last = float(t.last) if t.last and t.last > 0 else None
        close = float(t.close) if t.close and t.close > 0 else None
        # `marketPrice()` falls back through last → midpoint → close
        mark = float(t.marketPrice()) if not pd.isna(t.marketPrice()) else None

        mid = ((bid + ask) / 2) if (bid is not None and ask is not None) else None
        # Fetch daily history INSIDE the IB connection so we can compute SMA
        # fresh from IBKR data (no more stale Stooq CSV dependency).
        ctx = _sma_context_from_ib(ib, stock)
    finally:
        if own:
            ib.disconnect()

    snap = {
        "snapshot_ts": datetime.now().isoformat(timespec="seconds"),
        "ticker":      "SPY",
        "source":      "IBKR (delayed)" if live_config.IB_MKT_DATA_TYPE == 3 else "IBKR",
        "bid":         bid,
        "ask":         ask,
        "mid":         round(mid, 2) if mid else None,
        "last":        last,
        "mark":        round(mark, 2) if mark else None,
        "prev_close":  close,
        "change":      round(mark - close, 2) if (mark and close) else None,
        "change_pct":  round((mark - close) / close * 100, 2) if (mark and close) else None,
        **ctx,
    }
    # Live regime: compare live intraday mark to the 100d SMA from the daily CSV.
    # SMA is the mean of the last 100 closing prices ending on sma_as_of.
    if snap.get("mark") and snap.get("sma_100"):
        snap["live_regime"]     = "bull" if snap["mark"] > snap["sma_100"] else "bear"
        snap["live_vs_sma"]     = round(snap["mark"] - snap["sma_100"], 2)
        snap["live_vs_sma_pct"] = round((snap["mark"] - snap["sma_100"]) / snap["sma_100"] * 100, 2)
    return snap


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _run_once(ib: "IB | None" = None, do_print: bool = False) -> int:
    """One tick: fetch (two attempts), validate, write. ib=None logs in for the call."""
    snap = None
    for attempt in (1, 2):
        try:
            snap = fetch(ib)
            break
        except Exception as e:
            print(f"  ✗ fetch attempt {attempt} failed: {e}", flush=True)
            if attempt == 1:
                time.sleep(5)  # brief pause before retry; IBKR usually recovers
    if snap is None:
        print(f"  ✗ both attempts failed; preserving previous snapshot", flush=True)
        return 1

    # A connected Gateway can still stop delivering quote fields. Do not
    # replace the last valid tick with an all-null payload: that would make
    # freshness monitoring treat a market-data outage as a successful update.
    if snap.get("mark") is None:
        print("  ✗ SPY returned no quote; preserving previous snapshot", flush=True)
        return 1

    _atomic_write_json(OUT_PATH, snap)

    summary = (f"SPY {snap.get('mark', '—')}  "
               f"chg {snap.get('change')} ({snap.get('change_pct')}%)  "
               f"bid/ask {snap.get('bid')}/{snap.get('ask')}  "
               f"vs SMA {snap.get('live_vs_sma_pct')}%  "
               f"[{snap.get('live_regime', '?')}]")
    print(f"  ✓ {snap['snapshot_ts']}  {summary}", flush=True)
    print(f"  ✓ wrote {OUT_PATH}", flush=True)

    if do_print:
        print()
        print(json.dumps(snap, indent=2))
    return 0


# Persistent mode (2026-11-09), same protocol as live/fetcher.py serve():
#   live/logs/fetchd/spy.pid      daemon pid
#   live/logs/fetchd/spy.trigger  JSON {log, hhmm}
#   live/logs/fetchd/spy.done     JSON {status, elapsed}
# The connection is held between scans, so the tick never logs in during the fetch burst.
def _fetchd_dir() -> Path:
    d = Path(live_config.LOGS_DIR) / "fetchd"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _connect(ib: "IB", client_id: int) -> None:
    attempts = int(live_config.IB_CONNECT_ATTEMPTS)
    for attempt in range(1, attempts + 1):
        try:
            ib.connect(live_config.IB_HOST, live_config.IB_PORT, clientId=client_id,
                       readonly=True, timeout=float(live_config.IB_CONNECT_TIMEOUT))
            return
        except Exception as e:
            try:
                ib.disconnect()
            except Exception:
                pass
            if attempt == attempts:
                raise
            print(f"[spyd] connect attempt {attempt}/{attempts} failed ({e}); retrying", flush=True)
            time.sleep(2.0 * attempt)


def serve(client_id: int) -> int:
    import contextlib
    d = _fetchd_dir()
    (d / "spy.pid").write_text(str(os.getpid()))
    trig, done = d / "spy.trigger", d / "spy.done"
    ib = IB()
    try:
        _connect(ib, client_id)
        print(f"[spyd] connected, client {client_id}, pid {os.getpid()}", flush=True)
    except Exception as e:
        print(f"[spyd] not connected yet ({e}); retrying on the first trigger", flush=True)
    while True:
        try:
            if not trig.exists():
                ib.sleep(0.2)
                continue
            try:
                job = json.loads(trig.read_text())
            except Exception as e:
                print(f"[spyd] bad trigger ({e}); ignored", flush=True)
                trig.unlink(missing_ok=True)
                continue
            trig.unlink(missing_ok=True)
            done.unlink(missing_ok=True)
            log_path = Path(job.get("log") or (d / "spy.job.log"))
            t0 = time.monotonic()
            status = 1
            with open(log_path, "a") as fh, contextlib.redirect_stdout(fh):
                try:
                    if not ib.isConnected():
                        print("  [spyd] reconnecting", flush=True)
                        _connect(ib, client_id)
                    status = _run_once(ib)
                except Exception as e:
                    print(f"  ✗ spy daemon job failed: {e!r}", flush=True)
                    try:
                        ib.disconnect()      # a broken session reconnects on the next trigger
                    except Exception:
                        pass
            result = {"status": int(status), "elapsed": round(time.monotonic() - t0, 1)}
            tmp = done.with_suffix(".tmp")
            tmp.write_text(json.dumps(result))
            os.replace(tmp, done)
            print(f"[spyd] job {job.get('hhmm')} -> {result}", flush=True)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[spyd] loop error: {e!r}", flush=True)
            ib.sleep(1.0)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true",
                        help="Pretty-print the snapshot to stdout (in addition to writing)")
    parser.add_argument("--serve", action="store_true",
                        help="Hold the IB connection and refresh the tick on trigger files (see serve())")
    parser.add_argument("--client-id", type=int, default=live_config.IB_CLIENT_ID + 1)
    args = parser.parse_args()
    if args.serve:
        return serve(args.client_id)
    return _run_once(None, do_print=args.print)


if __name__ == "__main__":
    sys.exit(main())
