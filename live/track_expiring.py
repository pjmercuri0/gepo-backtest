"""Friday tracker: capture mark/P&L for picks expiring today.

The regular `track_frozen.py` reads from the daily snapshot parquet,
which only contains options in the main fetcher's DTE window
(`live_config.LIVE_DTE_MIN/MAX`, default 1-7). On Fridays, picks from the
prior week expire that day (DTE 0) and aren't in the snapshot, so the
regular tracker can only capture spot — never mark.

This script directly fetches each expiring pick's short+long contracts
from IB Gateway, computes mark/P&L using the same formulas as the
regular tracker, and appends a tracking row. It does NOT affect the
ranker or the freeze step — purely a tracking-side fix for the
expiration-day visibility gap.

Cron suggestion (Fridays during market hours):
  15 10-16 * * 5 cd /path/to/repo && python3 -m live.track_expiring

Usage:
  python3 -m live.track_expiring                 # use default client ID 202
  python3 -m live.track_expiring --client-id 250 # override
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live import credit_basis, live_config

try:
    from ib_insync import IB, Option, Stock
except ImportError as e:
    raise SystemExit("ib_insync not installed. `pip install ib_insync`") from e


def _is_expiring_today(payload: dict, today: date) -> bool:
    """Worth tracking iff outcome is unset AND expiry is today."""
    if payload.get("outcome"):
        return False
    picks = payload.get("top_picks") or []
    if not picks:
        return False
    exp_str = picks[0].get("expiry_date", "")
    try:
        return datetime.fromisoformat(exp_str).date() == today
    except (ValueError, TypeError):
        return False


def _right_for(spread_type: str) -> str:
    return "P" if spread_type == "bull_put" else "C"


def _atomic_write(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _fetch_pick(ib: IB, pick: dict) -> dict | None:
    """Fetch the pick's short+long option mid prices and the underlying spot
    directly from IB. Returns a tracking-row dict (compatible with the
    regular tracker's row shape) or None if everything failed."""
    sym    = pick["ticker"]
    expiry = pick["expiry_date"][:10].replace("-", "")
    right  = _right_for(pick["spread_type"])

    short = Option(sym, expiry, float(pick["short_strike"]), right,
                   "SMART", currency="USD", multiplier="100")
    long_ = Option(sym, expiry, float(pick["long_strike"]), right,
                   "SMART", currency="USD", multiplier="100")

    s_bid = s_ask = l_bid = l_ask = None
    try:
        qual = ib.qualifyContracts(short, long_)
        if len(qual) == 2 and all(c.conId for c in qual):
            s_tk, l_tk = ib.reqTickers(short, long_)
            s_bid = float(s_tk.bid) if s_tk.bid and s_tk.bid > 0 else None
            s_ask = float(s_tk.ask) if s_tk.ask and s_tk.ask > 0 else None
            l_bid = float(l_tk.bid) if l_tk.bid and l_tk.bid > 0 else None
            l_ask = float(l_tk.ask) if l_tk.ask and l_tk.ask > 0 else None
    except Exception as e:
        print(f"    [{sym}] option fetch failed: {e}", flush=True)

    # Underlying spot is fetched separately so we always have it even when
    # the option BBO is unavailable (e.g., last few minutes before expiry).
    underlying = None
    try:
        stock = Stock(sym, "SMART", "USD")
        ib.qualifyContracts(stock)
        [st] = ib.reqTickers(stock)
        mp = st.marketPrice()
        if mp and not pd.isna(mp):
            underlying = float(mp)
        elif st.last and not pd.isna(st.last):
            underlying = float(st.last)
        elif st.close and not pd.isna(st.close):
            underlying = float(st.close)
    except Exception as e:
        print(f"    [{sym}] spot fetch failed: {e}", flush=True)

    ts = datetime.now().isoformat(timespec="seconds")

    # No option BBO: spot-only row (matches the regular tracker's fallback).
    if None in (s_bid, s_ask, l_bid, l_ask):
        if underlying is None:
            return None
        return {"ts": ts, "underlying_price": round(underlying, 2)}

    short_mid = (s_bid + s_ask) / 2.0
    long_mid  = (l_bid + l_ask) / 2.0
    current_mark = round(short_mid - long_mid, 4)
    # Canonical shared basis (actual_credit > 0.80×MID). Was the RAW stored
    # net_credit with no fill haircut, which overstated P&L against every
    # other surface.
    entry_credit = credit_basis.entry_credit(pick)
    max_loss     = round(credit_basis.spread_width(pick) - entry_credit, 4)
    pnl_per_contract = round((entry_credit - current_mark) * 100, 2)
    pct_realized = round(((entry_credit - current_mark) / entry_credit) * 100, 2) \
        if entry_credit > 0 else 0.0

    return {
        "ts":                          ts,
        "short_bid":                   round(s_bid, 4),
        "short_ask":                   round(s_ask, 4),
        "long_bid":                    round(l_bid, 4),
        "long_ask":                    round(l_ask, 4),
        "underlying_price":            round(underlying, 2) if underlying is not None else None,
        "current_mark":                current_mark,
        "entry_credit":                entry_credit,
        "max_loss":                    max_loss,
        "unrealized_pnl_per_contract": pnl_per_contract,
        "pct_max_win_realized":        pct_realized,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", type=int, default=202,
                        help="IB Gateway clientId (default 202)")
    parser.add_argument("--date", type=str, default=None,
                        help="Process only the frozen file dated YYYY-MM-DD "
                             "(used by parallel cron fan-out). Skips expiry "
                             "check — caller has already verified.")
    args = parser.parse_args()

    today = date.today()
    frozen_dir = Path(live_config.FROZEN_DIR)
    expiring: list[tuple[Path, dict]] = []
    if args.date:
        fp = frozen_dir / f"{args.date}.json"
        try:
            with open(fp) as f:
                payload = json.load(f)
            expiring.append((fp, payload))
        except (json.JSONDecodeError, OSError, FileNotFoundError) as e:
            print(f"  ✗ could not load {fp.name}: {e}", flush=True)
            return 1
    else:
        for fp in sorted(frozen_dir.glob("*.json")):
            try:
                with open(fp) as f:
                    payload = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            if _is_expiring_today(payload, today):
                expiring.append((fp, payload))

    if not expiring:
        print("No frozen files with picks expiring today.", flush=True)
        return 0

    print(f"Tracking {len(expiring)} expiring frozen file(s) on {today}...", flush=True)

    ib = IB()
    try:
        ib.connect(live_config.IB_HOST, live_config.IB_PORT, clientId=args.client_id)
    except Exception as e:
        print(f"  ✗ IB connect failed: {e}", flush=True)
        return 1
    ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)

    try:
        for fp, payload in expiring:
            picks = payload.get("top_picks") or []
            print(f"  {fp.name}: {len(picks)} picks", flush=True)
            tracking = payload.setdefault("tracking", {})
            for pick in picks:
                tk = pick["ticker"]
                row = _fetch_pick(ib, pick)
                if row is None:
                    print(f"    [{tk}] no data", flush=True)
                    continue
                tracking.setdefault(tk, []).append(row)
                if "current_mark" in row:
                    print(f"    [{tk}] mark=${row['current_mark']}  "
                          f"P&L=${row['unrealized_pnl_per_contract']:+.2f}  "
                          f"({row['pct_max_win_realized']:+.1f}% of max win)", flush=True)
                else:
                    print(f"    [{tk}] spot=${row.get('underlying_price')}  "
                          f"(no option BBO)", flush=True)
            _atomic_write(fp, payload)
    finally:
        ib.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
