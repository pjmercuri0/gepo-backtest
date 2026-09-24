"""Always-on combo quote stream for the ranked board.

Why: combo quotes arrive ~2-3s after you subscribe, so a cold requote cannot be
done in a second (measured 2026-09-16: a 12-bag batch got 1/12 in 1.5s, 8/12 in
3.3s). Holding the subscriptions open removes the wait entirely -- ib_insync
updates the Ticker objects in place, so reading them is instant.

Loop:
  - re-read live/ranked/latest.json every RELOAD_S to learn the current board
  - resolve leg conIds from the snapshot parquet that payload names
  - subscribe to bags that are new, cancel bags that fell off
  - write live/ranked/combo_stream.json every WRITE_S

The webapp overlays that file onto /api/latest.json, so the tab shows quotes a
second old instead of up to a scan old. Selection is untouched: the ranker still
scores on the model credit and the file only carries quotes.

Read-only IBKR, client id 111 (110 is the batch combo fetch, 100-109 fetchers).
Idempotent: exits if another copy holds the lock.
"""
from __future__ import annotations
import json, os, shlex, subprocess, sys, time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ib_insync import IB, Stock, Option, Bag, ComboLeg, Contract
from live import live_config
from live.combo_quotes import _bag_for
from live.fetcher import _connect_with_retry

CLIENT_ID = int(getattr(live_config, "LIVE_STREAM_CLIENT_ID", 111))
# IBKR caps concurrent market-data lines (error 101 "Max number of tickers has been
# reached" at 100 on this account). Budget: open positions always get a line, the
# ranked board fills what is left, and spots are capped separately.
MAX_BAGS  = int(getattr(live_config, "LIVE_STREAM_MAX_BAGS", 26))
MAX_SPOTS = int(getattr(live_config, "LIVE_STREAM_MAX_SPOTS", 40))
RELOAD_S  = float(getattr(live_config, "LIVE_STREAM_RELOAD_S", 20))
WRITE_S   = float(getattr(live_config, "LIVE_STREAM_WRITE_S", 1.0))
OUT   = Path(live_config.RANKED_DIR) / "combo_stream.json"
PUBLISH_S = float(getattr(live_config, "LIVE_STREAM_PUBLISH_S", 2.0))
LOCK  = ROOT / "live" / "logs" / "combo_stream.lock"


def _key(r) -> str:
    return f"{r['ticker']}|{r['spread_type']}|{float(r['short_strike']):g}|{float(r['long_strike']):g}|{str(r['expiry_date'])[:10]}"


def _positions(ib) -> list[dict]:
    """Open Actuals rows as streamable spreads (2026-09-16). Held strikes are often
    outside the scan's fetch band -- a position that ran deep ITM is exactly the one
    the fetcher stops pulling -- so conIds are qualified straight from IBKR rather
    than looked up in the snapshot."""
    fp = ROOT / "live" / "actuals.json"
    if not fp.exists():
        return []
    try:
        store = json.loads(fp.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    today = datetime.now().date().isoformat()
    out = []
    for t in store.get("trades") or []:
        p = t.get("pick") or {}
        exp = str(p.get("expiry_date") or "")[:10]
        if not exp or exp < today or p.get("pnl") is not None:
            continue
        right = "P" if p.get("spread_type") == "bull_put" else "C"
        legs = []
        for k in ("short_strike", "long_strike"):
            try:
                o = Option(p["ticker"], exp.replace("-", ""), float(p[k]), right, "SMART", currency="USD")
            except (KeyError, TypeError, ValueError):
                legs = []; break
            if not ib.qualifyContracts(o) or not o.conId:
                legs = []; break
            legs.append(o.conId)
        if len(legs) != 2:
            continue
        out.append({**p, "short_conid": legs[0], "long_conid": legs[1], "_key": _key(p)})
    return out


def _board() -> list[dict]:
    """Ranked rows with leg conIds resolved from the payload's own snapshot."""
    payload = json.loads((Path(live_config.RANKED_DIR) / "latest.json").read_text())
    rows = (payload.get("ticker") or [])[:MAX_BAGS]   # trimmed again after positions
    snap_path = ROOT / str(payload.get("snapshot_file") or "")
    if not snap_path.exists():
        return []
    snap = pd.read_parquet(snap_path, columns=["Symbol", "ExpirationDate", "StrikePrice", "PutCall", "conId"])
    snap["exp"] = pd.to_datetime(snap.ExpirationDate).dt.strftime("%Y-%m-%d")
    snap["pc"] = snap.PutCall.astype(str).str.lower()
    idx = {(r.Symbol, r.exp, float(r.StrikePrice), r.pc): int(r.conId) for r in snap.itertuples()}
    out = []
    for r in rows:
        exp = str(r.get("expiry_date"))[:10]
        pc = "put" if r.get("spread_type") == "bull_put" else "call"
        s = idx.get((r.get("ticker"), exp, float(r.get("short_strike")), pc))
        l = idx.get((r.get("ticker"), exp, float(r.get("long_strike")), pc))
        if s and l:
            out.append({**r, "short_conid": s, "long_conid": l, "_key": _key(r)})
    return out


def _atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def _publish(last_at: float) -> float:
    """rsync the quote file to Mya. Mya has no IBKR, so the tab can only show live
    quotes if the mini pushes them. Uses an ssh ControlMaster socket so each push is
    a few tens of ms, not a fresh handshake."""
    host = os.environ.get("MYA_SSH_HOST")
    if not host or time.monotonic() - last_at < PUBLISH_S:
        return last_at
    base = os.environ.get("MYA_REMOTE_BASE", "/opt/vito/gepo-backtest/live")
    sock = "/tmp/gepo_combo_stream_%r@%h:%p"
    ssh = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ControlMaster=auto", "-o", f"ControlPath={sock}",
        "-o", "ControlPersist=300",
    ]
    rsync_ssh = " ".join(shlex.quote(arg) for arg in ssh)
    destination = f"{host}:{base}/ranked/"
    subprocess.Popen(
        ["rsync", "-az", "--timeout=5", "-e", rsync_ssh, str(OUT), destination],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return time.monotonic()


def main() -> int:
    try:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode()); os.close(fd)
    except FileExistsError:
        try:
            pid = int(LOCK.read_text().strip()); os.kill(pid, 0)
            print(f"[combo_stream] already running (pid {pid})", flush=True); return 0
        except (ValueError, ProcessLookupError, PermissionError):
            LOCK.unlink(missing_ok=True)
            fd = os.open(LOCK, os.O_CREAT | os.O_WRONLY); os.write(fd, str(os.getpid()).encode()); os.close(fd)

    ib = IB()
    subs: dict[str, tuple] = {}          # key -> (bag, ticker)
    spots: dict[str, tuple] = {}         # symbol -> (contract, ticker)
    legs: dict[str, tuple] = {}          # key -> (short_conid, long_conid)
    widths: dict[str, float] = {}        # key -> spread width, for the sanity check
    leg_c: dict[int, object] = {}        # conid -> contract
    leg_t: dict[int, object] = {}        # conid -> ticker
    try:
        _connect_with_retry(ib, CLIENT_ID)
        ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)
        print(f"[combo_stream] connected, client {CLIENT_ID}, max {MAX_BAGS} bags", flush=True)
        last_reload = 0.0
        last_pub = 0.0
        while True:
            now = time.monotonic()
            if now - last_reload >= RELOAD_S:
                last_reload = now
                try:
                    board = _board()
                except Exception as e:
                    board = []
                    print(f"[combo_stream] board read failed: {e}", flush=True)
                try:
                    pos = _positions(ib)
                except Exception as e:
                    pos = []
                    print(f"[combo_stream] positions read failed: {e}", flush=True)
                seen = {r["_key"] for r in pos}
                board = pos + [r for r in board if r["_key"] not in seen][:max(0, MAX_BAGS - len(pos))]
                want = {r["_key"]: r for r in board}
                for k in list(subs):
                    if k not in want:
                        bag, _t = subs.pop(k)
                        try: ib.cancelMktData(bag)
                        except Exception: pass
                for k, r in want.items():
                    legs[k] = (r.get("short_conid"), r.get("long_conid"))
                    try:
                        widths[k] = float(r.get("spread_width") or abs(float(r["short_strike"]) - float(r["long_strike"])))
                    except (TypeError, ValueError, KeyError):
                        widths[k] = None
                    if k in subs:
                        continue
                    bag = _bag_for(r)
                    if bag is None:
                        continue
                    try:
                        subs[k] = (bag, ib.reqMktData(bag, "", False, False))
                    except Exception as e:
                        print(f"[combo_stream] subscribe failed {k}: {e}", flush=True)
                # Leg subscriptions: the combo book is usually empty, so the legs are
                # what actually price the spread. Deduped -- adjacent spreads share strikes.
                want_legs = {c for k in want for c in legs.get(k, ()) if c}
                for cid in list(leg_c):
                    if cid not in want_legs:
                        try: ib.cancelMktData(leg_c.pop(cid))
                        except Exception: pass
                        leg_t.pop(cid, None)
                for cid in want_legs:
                    if cid in leg_c:
                        continue
                    try:
                        c = Contract(conId=int(cid), exchange="SMART")
                        if ib.qualifyContracts(c):
                            leg_c[cid] = c
                            leg_t[cid] = ib.reqMktData(c, "", False, False)
                    except Exception as e:
                        print(f"[combo_stream] leg subscribe failed {cid}: {e}", flush=True)
                # Underlying spot too (2026-09-16): the page colours pin/status and shows
                # the cushion off spot, so a streaming credit beside a scan-old spot reads
                # inconsistently. One line per distinct ticker.
                want_syms = list(dict.fromkeys(r["ticker"] for r in board))[:MAX_SPOTS]
                for sym in list(spots):
                    if sym not in want_syms:
                        c, _t = spots.pop(sym)
                        try: ib.cancelMktData(c)
                        except Exception: pass
                for sym in want_syms:
                    if sym in spots:
                        continue
                    try:
                        c = Stock(sym, "SMART", "USD")
                        if ib.qualifyContracts(c):
                            spots[sym] = (c, ib.reqMktData(c, "", False, False))
                    except Exception as e:
                        print(f"[combo_stream] spot subscribe failed {sym}: {e}", flush=True)
                print(f"[combo_stream] holding {len(subs)} combos, {len(leg_c)} legs, {len(spots)} spots", flush=True)

            ib.sleep(WRITE_S)
            ts = datetime.now().isoformat(timespec="seconds")
            quotes = {}
            ok = lambda v: v is not None and v == v and v != 0          # not None/NaN/0
            for k, (_bag, t) in subs.items():
                bid, ask, last = t.bid, t.ask, t.last
                if ok(bid) and ok(ask):
                    _w = widths.get(k)
                    # Validate EACH SIDE against [0, width] before averaging. A
                    # vertical cannot be worth more than its width, so a quote
                    # outside that is junk -- and averaging it in poisons the mid.
                    # AMGN 405/402.5 on 2026-09-24 streamed bid -4.87 / ask -0.05
                    # on a 2.50-wide spread: the bid is impossible, but the mean
                    # landed at 2.46, inside [0, width], so the old clamp passed
                    # it. A WINNING spread (spot 407.83, short 405) was marked at
                    # near max loss and showed -$80.
                    _cb, _ca = -float(bid), -float(ask)          # cost to close
                    _lo, _hi = (0.0, _w + 1e-9) if _w else (0.0, float("inf"))
                    _sides = [v for v in (_cb, _ca) if _lo <= v <= _hi]
                    _m = (sum(_sides) / len(_sides)) if _sides else -1.0
                    if _m > 0 and (_w is None or _m <= _w + 1e-9):
                        quotes[k] = {"bid": float(bid), "ask": float(ask), "mid": round(_m, 4),
                                     "last": (float(last) if ok(last) else None),
                                     "src": "combo", "ts": ts}
                        continue
                # 2026-09-17: most of these bags never quote -- FCX 72/71 returned
                # nan while TWS showed -0.68/-0.29/-0.48. TWS derives those from the
                # LEGS: bid = short_ask - long_bid, ask = short_bid - long_ask,
                # mid = short_mid - long_mid. Do the same so a missing combo book no
                # longer falls back to the scan's stale leg mids.
                lg = legs.get(k)
                if not lg:
                    continue
                st_, lt_ = leg_t.get(lg[0]), leg_t.get(lg[1])
                if not st_ or not lt_:
                    continue
                if not (ok(st_.bid) and ok(st_.ask) and ok(lt_.bid) and ok(lt_.ask)):
                    continue
                s_mid = (float(st_.bid) + float(st_.ask)) / 2.0
                l_mid = (float(lt_.bid) + float(lt_.ask)) / 2.0
                mid = s_mid - l_mid
                # Sanity (2026-09-17, after the first evening this ran): leg quotes decay
                # once the bell goes -- sizes drop out, one side stops updating -- and the
                # subtraction then yields 0.0 or a NEGATIVE spread value. A short vertical
                # is always worth between 0 and its width, and the short leg (nearer the
                # money) is always worth more than the long. Anything else is not a quote,
                # so publish nothing and let the caller fall back to BS.
                wid = widths.get(k)
                if not (s_mid > l_mid and mid > 0 and (wid is None or mid <= wid + 1e-9)):
                    continue
                quotes[k] = {"bid": round(-(float(st_.ask) - float(lt_.bid)), 4),
                             "ask": round(-(float(st_.bid) - float(lt_.ask)), 4),
                             "mid": round(mid, 4),
                             "last": None, "src": "legs", "ts": ts}
            sp = {}
            for sym, (_c, t) in spots.items():
                v = t.last if (t.last is not None and t.last == t.last and t.last != 0) else t.close
                if v is not None and v == v and v != 0:
                    sp[sym] = round(float(v), 4)
            _atomic(OUT, {"ts": ts, "n": len(quotes), "held": len(subs),
                          "quotes": quotes, "spots": sp})
            last_pub = _publish(last_pub)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            for _k, (bag, _t) in subs.items():
                try: ib.cancelMktData(bag)
                except Exception: pass
            for _s, (c, _t) in spots.items():
                try: ib.cancelMktData(c)
                except Exception: pass
            for _cid, c in leg_c.items():
                try: ib.cancelMktData(c)
                except Exception: pass
            if ib.isConnected():
                ib.disconnect()
        finally:
            LOCK.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
