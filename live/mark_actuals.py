"""Price the OPEN Actuals positions off their own strikes, every scan.

Why this exists: the scanner fetches a strike band sized to find NEW candidates
near spot -- 1 sigma x sqrt(DTE/365), floored at +/-2%. At 1 DTE that floor
binds, so a position whose price has moved falls outside the band and stops
being fetched in EITHER direction. On 2026-09-24 with expiry the next day:

    AAPL spot 335.51  band [328.80, 342.22]  held 342.5/340  -> not fetched
    CSCO spot 105.81  band [103.65, 107.97]  held 111/110    -> not fetched
    XOM  spot 164.13  band [160.85, 167.41]  held 160/157.5  -> not fetched

With no quotes and no IVs the webapp fell through to INTRINSIC, which for a
spread below both strikes is the full width -- so AAPL marked at max loss while
the real market was 1.80, and XOM marked at zero. Those artifacts are what made
the Actuals P&L disagree with IBKR all week.

This asks IBKR for the exact legs of every position the user holds, regardless
of any band, and writes the marks to live/ranked/actuals_marks.json. The webapp
prefers that file, so an open position is always priced off its own book.

Read-only IBKR, client id 120 (100-109 fetchers, 111 combo stream).
Idempotent: exits if another copy holds the lock.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date as ddate, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ib_insync import IB, Option

from live import live_config
from live.fetcher import _connect_with_retry

CLIENT_ID = int(getattr(live_config, "MARK_ACTUALS_CLIENT_ID", 120))
OUT = Path(live_config.RANKED_DIR) / "actuals_marks.json"
LOCK = Path(live_config.ROOT_DIR) / "logs" / "mark_actuals.lock"
QUOTE_WAIT_S = float(getattr(live_config, "MARK_ACTUALS_WAIT_S", 6.0))


def _key(p: dict) -> str:
    return (f"{p.get('ticker')}|{p.get('spread_type')}|"
            f"{float(p['short_strike']):g}|{float(p['long_strike']):g}|"
            f"{str(p.get('expiry_date'))[:10]}")


def open_positions() -> list[dict]:
    """Every distinct Actuals spread that has not expired."""
    path = Path(live_config.ROOT_DIR) / "actuals.json"
    try:
        store = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    today = ddate.today().isoformat()
    out, seen = [], set()
    for t in (store.get("trades") or []):
        p = t.get("pick") or {}
        exp = str(p.get("expiry_date") or "")[:10]
        if not exp or exp < today:
            continue
        try:
            k = _key(p)
        except (KeyError, TypeError, ValueError):
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


def _leg_mid(t) -> float | None:
    """Mid of one option leg; None when the book is unusable."""
    bid, ask = t.bid, t.ask
    for v in (bid, ask):
        if v is None or v != v or v < 0:
            return None
    if ask < bid:
        return None
    return (float(bid) + float(ask)) / 2.0


def mark_positions(ib: IB, picks: list[dict]) -> dict:
    """{key: {mark, bid, ask, iv_short, iv_long, basis}} priced off the real legs."""
    marks: dict[str, dict] = {}
    if not picks:
        return marks

    contracts, index = [], []
    for p in picks:
        exp = str(p.get("expiry_date"))[:10].replace("-", "")
        right = "P" if p.get("spread_type") == "bull_put" else "C"
        for leg, strike in (("short", p["short_strike"]), ("long", p["long_strike"])):
            contracts.append(Option(p["ticker"], exp, float(strike), right, "SMART"))
            index.append((_key(p), leg))

    try:
        qualified = ib.qualifyContracts(*contracts)
    except Exception as exc:                                   # noqa: BLE001
        print(f"[mark_actuals] qualifyContracts failed: {exc}", flush=True)
        return marks

    live_c, live_i = [], []
    for c, meta in zip(qualified, index):
        if getattr(c, "conId", 0):
            live_c.append(c)
            live_i.append(meta)
    if not live_c:
        print("[mark_actuals] no legs qualified", flush=True)
        return marks

    from ib_insync import Stock
    syms = sorted({p["ticker"] for p in picks})
    stocks = ib.qualifyContracts(*[Stock(sy, "SMART", "USD") for sy in syms])
    stock_t = [ib.reqMktData(c, "", False, False) for c in stocks if getattr(c, "conId", 0)]

    tickers = [ib.reqMktData(c, "", False, False) for c in live_c]
    ib.sleep(QUOTE_WAIT_S)

    spot_of = {}
    for t in stock_t:
        v = t.last if (t.last and t.last == t.last) else t.close
        if v and v == v:
            spot_of[t.contract.symbol] = float(v)
        ib.cancelMktData(t.contract)

    legs: dict[str, dict] = {}
    for (k, leg), t in zip(live_i, tickers):
        g = t.modelGreeks
        legs.setdefault(k, {})[leg] = {
            "mid": _leg_mid(t),
            "iv": (float(g.impliedVol) if g and g.impliedVol is not None else None),
        }
    for c in live_c:
        ib.cancelMktData(c)

    ts = datetime.now().isoformat(timespec="seconds")
    for p in picks:
        k = _key(p)
        pair = legs.get(k) or {}
        s, l = pair.get("short") or {}, pair.get("long") or {}
        width = abs(float(p["short_strike"]) - float(p["long_strike"]))
        row = {"ts": ts, "width": round(width, 4),
               "iv_short": s.get("iv"), "iv_long": l.get("iv")}
        sm, lm = s.get("mid"), l.get("mid")
        if sm is not None and lm is not None:
            mark = sm - lm
            # A vertical is worth 0..width. Anything outside is a bad book, not
            # a number to show -- the same rule combo_stream applies.
            if 0.0 <= mark <= width + 1e-9:
                row["mark_legs"] = round(mark, 4)

        # Black-Scholes off the IVs of THESE legs (user, 2026-09-24). The
        # webapp's own BS path had to guess IVs from the day's snapshot, which
        # does not carry a strike once it leaves the scan band -- that is how
        # AAPL ended up at max loss. These IVs come from the option itself.
        if s.get("iv") and l.get("iv") and spot_of.get(p["ticker"]):
            try:
                from live.bs_pricing import bs_spread_debit
                dte = max((ddate.fromisoformat(str(p["expiry_date"])[:10])
                           - ddate.today()).days, 0)
                bs = bs_spread_debit(
                    spot=float(spot_of[p["ticker"]]),
                    short_strike=float(p["short_strike"]),
                    long_strike=float(p["long_strike"]),
                    short_iv=float(s["iv"]), long_iv=float(l["iv"]),
                    dte_days=dte, spread_type=p["spread_type"])
                if bs is not None and 0.0 <= bs <= width + 1e-9:
                    row["mark_bs"] = round(float(bs), 4)
                    row["dte"] = dte
            except Exception as exc:                           # noqa: BLE001
                print(f"[mark_actuals] BS failed {k}: {exc}", flush=True)

        # BS is the canonical mark (user choice); the leg mid is the fallback
        # and is kept for comparison.
        row["mark"] = row.get("mark_bs", row.get("mark_legs"))
        row["basis"] = ("BS at own-leg IV" if "mark_bs" in row
                        else ("own legs (mid)" if "mark_legs" in row else None))
        marks[k] = row
    return marks


def main() -> int:
    if LOCK.exists():
        try:
            os.kill(int(LOCK.read_text().strip()), 0)
            print("[mark_actuals] another copy is running", flush=True)
            return 0
        except (OSError, ValueError):
            LOCK.unlink(missing_ok=True)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()))

    try:
        picks = open_positions()
        if not picks:
            print("[mark_actuals] no open positions", flush=True)
            return 0
        ib = IB()
        try:
            _connect_with_retry(ib, CLIENT_ID)
        except Exception as exc:                               # noqa: BLE001
            print(f"[mark_actuals] could not connect: {exc}", flush=True)
            return 1
        if not ib.isConnected():
            print("[mark_actuals] could not connect", flush=True)
            return 1
        try:
            ib.reqMarketDataType(int(live_config.IB_MKT_DATA_TYPE))
            marks = mark_positions(ib, picks)
        finally:
            ib.disconnect()

        priced = sum(1 for v in marks.values() if v.get("mark") is not None)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"ts": datetime.now().isoformat(timespec="seconds"),
             "n": len(marks), "priced": priced, "marks": marks}, indent=1))
        tmp.replace(OUT)
        print(f"[mark_actuals] priced {priced}/{len(marks)} positions -> {OUT}", flush=True)
        return 0
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
